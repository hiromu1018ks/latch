"""ws-4受け入れ(b): シナリオ遷移・sessions投与・Clock前進でKpiJob発火・MLR照合。

既存helpers_scenarioの作法(make_scenario_pair(api, db, key=, when=, name=)・
wait_for_latch・POST /v1/latches/{id}/response のbody={"response": ...})を
踏襲する(計画コードのsubjects=形式・/v1/latches/my・{"value": ...}からの
適応・報告書に記録)。
"""

from datetime import datetime, timedelta

from helpers_scenario import make_scenario_pair, wait_for_latch
from helpers_seed import seed_all
from helpers_wait import wait_until
from obs_results import record_item

from conftest import auth_header, get_clock


async def _proposed_exists(db, ids) -> bool:  # noqa: ANN001
    n = await db.fetchval(
        "SELECT count(*) FROM latch_status_events e"
        " JOIN latches l ON l.id = e.latch_id"
        " WHERE e.to_status='proposed'"
        " AND l.intent_ids && $1::uuid[]",
        [str(i) for i in ids],
    )
    return n > 0


async def _matched_delta(db, before: int) -> int:  # noqa: ANN001
    cur = await db.fetchval(
        "SELECT count(*) FROM latch_status_events WHERE to_status='matched'"
    )
    return cur - before


async def test_w4_b1_scenario_matched_and_sessions(api, db):
    """2人ペアを双方YES(matched遷移)・sessions投与(DAU素材)。"""
    before = await db.fetchval(
        "SELECT count(*) FROM latch_status_events WHERE to_status='matched'"
    )
    # 専用シナリオ(ws-3のhelpers_scenario再利用・s2実績のwhen値でシードと隔離)
    pair = await make_scenario_pair(
        api, db, key="w4-b1", name="w4b", when="2026-10-05T06:30:00+09:00"
    )

    async def proposed_appeared() -> bool:  # noqa: ANN202
        # wait_untilへはcoroutine関数を渡す(lambdaはcoroutineをawaitできない)
        return await _proposed_exists(db, pair["intent_ids"])

    await wait_until(
        proposed_appeared,
        timeout_s=180,
        interval_s=10,
        what="専用ペアのproposed遷移",
    )
    latch = await wait_for_latch(api, pair["subjects"][0], status="proposed")
    latch_id = str(latch["id"])
    r1 = await api.post(
        f"/v1/latches/{latch_id}/response",
        headers=auth_header(pair["subjects"][0]),
        json={"response": "yes"},
    )
    r2 = await api.post(
        f"/v1/latches/{latch_id}/response",
        headers=auth_header(pair["subjects"][1]),
        json={"response": "yes"},
    )
    assert r1.status_code == 200 and r2.status_code == 200, (r1.text, r2.text)

    async def matched_increased() -> bool:  # noqa: ANN202
        return await _matched_delta(db, before) >= 1

    await wait_until(
        matched_increased,
        timeout_s=180,
        interval_s=10,
        what="双方YESのmatched遷移",
    )
    # sessions投与(DAU素材・3ユーザー=ペア2人+シードの先頭ユーザー)
    state = await seed_all(api, db)
    subjects = list(pair["subjects"]) + [list(state["su_to_uuid"].keys())[0]]
    for subject in subjects:
        r = await api.post("/v1/sessions", headers=auth_header(subject))
        assert r.status_code == 204, f"sessions投与失敗 {subject}: {r.text}"
    record_item(
        item_id="ws4-b1",
        name="専用ペアmatchedとsessions投与",
        test="test_w4_b1_scenario_matched_and_sessions",
        verdict="pass",
        observed={"latch": latch_id, "sessions_users": len(subjects)},
    )


async def test_w4_b2_kpi_job_fires_on_clock_advance(api, db):
    """JST 0時跨ぎのClock操作→KpiJob発火(≤240秒)→kpi_dailyにmlr行が現れる。"""
    clock = await get_clock()
    state = await seed_all(api, db)  # 2回目=キャッシュ
    base = datetime.fromisoformat(state["meta_base"])

    async def mlr_rows() -> bool:  # noqa: ANN202
        n = await db.fetchval("SELECT count(*) FROM kpi_daily WHERE metric_key='mlr'")
        return n > 0

    if not await mlr_rows():
        target = (base + timedelta(days=1)).replace(hour=0, minute=5)
        clock.set(target)  # JST 0時跨ぎ(前進のみ・ISO明示: 2026-10-05T00:05+09:00)
        record_item(
            item_id="ws4-b2-clock",
            name="Clock前進(JST 0時跨ぎ)",
            test="test_w4_b2_kpi_job_fires_on_clock_advance",
            verdict="pass",
            observed={"clock_set_to": target.isoformat()},
        )
    await wait_until(
        mlr_rows,
        timeout_s=240,
        interval_s=15,
        what="KpiJobの遡及UPSERT(kpi_daily mlr行)",
    )
    record_item(
        item_id="ws4-b2",
        name="KpiJob発火(kpi_daily mlr行)",
        test="test_w4_b2_kpi_job_fires_on_clock_advance",
        verdict="pass",
        observed={
            "mlr_rows": await db.fetchval(
                "SELECT count(*) FROM kpi_daily WHERE metric_key='mlr'"
            )
        },
    )


async def test_w4_b3_mlr_matches_db_observation(api, db):
    """kpi_dailyのmlr分子/分母がDB実測と一致(G4-4の証拠・09 §2.1どおり)。"""
    row = await db.fetchrow(
        "SELECT numerator, denominator, day FROM kpi_daily"
        " WHERE metric_key='mlr' ORDER BY day DESC LIMIT 1"
    )
    assert row is not None, "kpi_daily mlr行が無い(KpiJob未発火)"
    day = row["day"]
    # 同一JST日のDB実測(ハーネス内で直接数える・09 §2.1の集計規定どおり)
    num = await db.fetchval(
        "SELECT count(*) FROM latch_status_events"
        " WHERE to_status='matched'"
        " AND (created_at AT TIME ZONE 'Asia/Tokyo')::date = $1",
        day,
    )
    den = await db.fetchval(
        "SELECT count(*) FROM latch_status_events"
        " WHERE to_status='proposed'"
        " AND (created_at AT TIME ZONE 'Asia/Tokyo')::date = $1",
        day,
    )
    assert (int(row["numerator"]), int(row["denominator"])) == (num, den)
    record_item(
        item_id="ws4-b3",
        name="MLR日次値とDB実測一致",
        test="test_w4_b3_mlr_matches_db_observation",
        verdict="pass",
        observed={
            "day": str(day),
            "kpi": [int(row["numerator"]), int(row["denominator"])],
            "db_observed": [num, den],
        },
    )
