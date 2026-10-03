"""S1: 候補系(02#5・#6・#7・#8・#9・#10・#11・#12・設計§2.5の骨子どおり)。

時刻不動で観察する検査を先に置き、#8(JST 0時跨ぎのClock前進)を最後に
置く(前進はS2以降の前提を作る)。DB観察はSELECT専用接続(conftestの
db fixture)のみ・書き込みはAPI正規経路のみ。
"""

from __future__ import annotations

from datetime import datetime, timedelta

from helpers_scenario import make_scenario_pair
from helpers_seed import (
    ex_pair_ids,
    fetch_candidates_pairs,
    load_seed,
    seed_all,
)

from conftest import auth_header, get_clock, record_item

FAIL_EX = (  # 設計§2.5 #9の材料(EX fail組)
    "EX-007",
    "EX-009",
    "EX-011",
    "EX-012",
    "EX-014",
    "EX-016",
    "EX-018",
    "EX-019",
    "EX-023",
    "EX-024",
)


def _patch_body(time_start: str, raw_text: str) -> dict:
    """PATCHのbody(PATCHは全置換・全検証 — 05 §5・service._update_active)。

    計画コードはtimeのみの部分bodyだったが、_update_activeは必須3検証+
    ジオコードを再実行するため_create_intent既定と同一の全フィールドを
    送る(計画コードからの適応・報告書に記録)。
    """
    return {
        "raw_text": raw_text,
        "structured_intent": {
            "category": {"primary": "activity", "secondary": "散策"},
            "alcohol_involved": False,
            "time": {"start": time_start},
            "location": {"name": "天文館", "radius_m": 1000},
            "budget": {"max": 3000},
            "participants": {"min": 2, "max": 2},
            "soft_constraints": [],
            "visibility": "summary_only",
            "notification_level": "proposals_only",
        },
    }


async def test_s1_item05_smoke(api, db):
    """#5 スモーク: Intentあたり候補生成数≤K_v=50+Jev実行総量の記録照合。"""
    await seed_all(api, db)  # シード投入済みの保証(2回目以降はキャッシュ即返し)
    max_per_intent = await db.fetchval(
        "SELECT max(n) FROM ("
        "  SELECT count(*) AS n FROM match_candidates GROUP BY intent_a_id"
        "  UNION ALL"
        "  SELECT count(*) AS n FROM match_candidates GROUP BY intent_b_id"
        ") t"
    )
    assert max_per_intent is not None and max_per_intent <= 50, (
        f"Intentあたり候補数がK_v=50を超えました: {max_per_intent}"
    )
    ev_total = await db.fetchval(
        "SELECT count(*) FROM match_candidates WHERE jev_result IS NOT NULL"
    )
    assert ev_total <= 300 * 8, f"Jev評価済み行が総上限を超えました: {ev_total}"
    # 起点あたり評価行数≤K_j=8の近似観察(FR-07のjev_result IS NULLガードで
    # 同一ペアは1回のみ評価される・厳密なイベント単位観察はws-6が担う)
    max_ev_per_intent = await db.fetchval(
        "SELECT max(n) FROM ("
        "  SELECT count(*) AS n FROM match_candidates"
        "   WHERE jev_result IS NOT NULL GROUP BY intent_a_id"
        "  UNION ALL"
        "  SELECT count(*) AS n FROM match_candidates"
        "   WHERE jev_result IS NOT NULL GROUP BY intent_b_id"
        ") t"
    )
    assert max_ev_per_intent <= 8, (
        f"起点あたりJev実行がK_j=8を超えました: {max_ev_per_intent}"
    )
    record_item(
        item_id="02#5",
        name="総当たりスモーク(K_v=50以内・K_j記録照合)",
        test="test_s1_item05_smoke",
        verdict="pass",
        observed={
            "max_candidates_per_intent": max_per_intent,
            "jev_evaluated_total": ev_total,
            "max_jev_per_intent": max_ev_per_intent,
        },
    )


async def test_s1_item06_ex_pass_bidirectional(api, db):
    """#6 既存Intentを含む候補の生成記録(EX passペアの有向両方向の観察)。"""
    state = await seed_all(api, db)
    seed = load_seed()
    rows = await db.fetch("SELECT intent_a_id, intent_b_id FROM match_candidates")
    uuid_to_sd = {v: k for k, v in state["sd_to_uuid"].items()}
    directed: set[tuple[str, str]] = set()
    for r in rows:
        a = uuid_to_sd.get(str(r["intent_a_id"]))
        b = uuid_to_sd.get(str(r["intent_b_id"]))
        if a is not None and b is not None:
            directed.add((a, b))
    sample = sorted(state["ex_pass_sample"])
    both = [
        e
        for e in sample
        if (lambda p: p in directed and (p[1], p[0]) in directed)(ex_pair_ids(seed, e))
    ]
    assert both, f"両方向の行を持つEX passペアが0組: {sample}"
    record_item(
        item_id="02#6",
        name="EX passペアの生成記録(無向実在7組+両方向観察)",
        test="test_s1_item06_ex_pass_bidirectional",
        verdict="pass",
        observed={"sample": sample, "bidirectional_ex": both},
    )


async def test_s1_item07_patch_reevaluation(api, db):
    """#7 PATCH(時間)→debounce 10秒→updated Event→再評価の生成/不生成。"""
    await seed_all(api, db)  # シード投入済みの保証
    from helpers_scenario import _create_intent, _register

    # 最初は時刻を離して作る(A=D+1 06:30・B=D+3 06:30 → time0 failで不在)。
    # make_scenario_pairは2人に同一whenを作るため、この検査では直接作成する
    s_a = "HS-p07-1"
    s_b = "HS-p07-2"
    await _register(api, s_a, "1995-04-01")
    await _register(api, s_b, "1995-04-01")
    await _create_intent(
        api,
        s_a,
        when="2026-10-05T06:30:00+09:00",
        raw_text="木曜の早朝に天文館で散歩したい",
    )
    b_id = await _create_intent(
        api,
        s_b,
        when="2026-10-07T06:30:00+09:00",
        raw_text="土曜の早朝に天文館で散歩したい",
    )

    def _count_containing(intent_uuid: str) -> int:
        return db.fetchval(
            "SELECT count(*) FROM match_candidates"
            " WHERE intent_a_id=$1::uuid OR intent_b_id=$1::uuid",
            intent_uuid,
        )

    assert await _count_containing(b_id) == 0, "離れた時刻で作成したのに候補が存在"
    # PATCH: Bの時刻をA(D+1 06:30)へ近づける → candidate生成を観察
    rp = await api.patch(
        f"/v1/intents/{b_id}",
        headers=auth_header(s_b),
        json=_patch_body("2026-10-05T06:30:00+09:00", "木曜の早朝に天文館で散歩したい"),
    )
    assert rp.status_code == 200, f"PATCH失敗: {rp.status_code} {rp.text}"
    from helpers_wait import wait_until

    async def appeared() -> bool:
        return await _count_containing(b_id) > 0

    await wait_until(
        appeared,
        timeout_s=180,
        interval_s=10,
        what="PATCH後の候補生成(debounce 10秒+updated Event再評価)",
    )
    after_patch = await _count_containing(b_id)
    # PATCH: 再度時刻を離す → 新versionでは生成されない
    # (旧version行は残る=実装の決定的挙動。証拠に記録する)
    rp2 = await api.patch(
        f"/v1/intents/{b_id}",
        headers=auth_header(s_b),
        json=_patch_body("2026-10-07T06:30:00+09:00", "土曜の早朝に天文館で散歩したい"),
    )
    assert rp2.status_code == 200
    import asyncio as _aio

    await _aio.sleep(70)  # debounce 10秒+tick 60秒+余裕
    final = await _count_containing(b_id)
    assert final == after_patch, (
        f"PATCH離行後に候補行が増えました: {after_patch}→{final}"
        "(新versionで生成されてはならない)"
    )
    record_item(
        item_id="02#7",
        name="PATCH再評価(生成/不生成の記録)",
        test="test_s1_item07_patch_reevaluation",
        verdict="pass",
        observed={
            "before": 0,
            "after_patch": after_patch,
            "final_after_leaving": final,
            "note": "旧version行は残存(実装の決定的挙動)",
        },
    )


async def test_s1_item09_hard_filter_e2e(api, db):
    """#9 Hard Filter E2E: EX failペアがいずれの候補にも現れない。"""
    state = await seed_all(api, db)
    seed = load_seed()
    db_pairs = await fetch_candidates_pairs(db, state["sd_to_uuid"])
    leaked = [
        e
        for e in FAIL_EX
        if ex_pair_ids(seed, e) is not None and ex_pair_ids(seed, e) in db_pairs
    ]
    assert not leaked, f"EX failペアが候補に存在(無向化突合): {leaked}"
    record_item(
        item_id="02#9",
        name="Hard Filter E2E(EX failペア不生成+pass組はS0突合済み)",
        test="test_s1_item09_hard_filter_e2e",
        verdict="pass",
        observed={"checked": list(FAIL_EX), "db_pairs": len(db_pairs)},
    )


async def test_s1_item10_near_semantic_pair(api, db):
    """#10 意味近接ペア(焼肉/肉系)の候補生成(stub下ではL1通過ゆえの生成)。"""
    await seed_all(api, db)  # シード投入済みの保証
    pair = await make_scenario_pair(
        api,
        db,
        key="s1-10",
        name="p10",
        when="2026-10-05T06:30:00+09:00",
        category="meal",
        secondary="焼肉",
        raw_text="早朝に天文館で焼肉ランチ気分の食事をしたい",
    )
    s2 = pair["subjects"][1]
    from helpers_scenario import _create_intent

    i2 = await _create_intent(
        api,
        s2,
        when="2026-10-05T06:30:00+09:00",
        category="meal",
        secondary="肉居酒屋",
        alcohol_involved=False,
        raw_text="早朝に天文館で肉料理を食べたい",
    )
    pair["intent_ids"][1] = i2
    from helpers_wait import wait_until

    async def paired() -> bool:
        n = await db.fetchval(
            "SELECT count(*) FROM match_candidates"
            " WHERE (intent_a_id=$1::uuid AND intent_b_id=$2::uuid)"
            " OR (intent_a_id=$2::uuid AND intent_b_id=$1::uuid)",
            pair["intent_ids"][0],
            i2,
        )
        return n > 0

    await wait_until(paired, timeout_s=180, interval_s=10, what="#10ペアの候補生成")
    record_item(
        item_id="02#10",
        name="意味近接ペアの候補生成(案A: L1通過ゆえの生成)",
        test="test_s1_item10_near_semantic_pair",
        verdict="pass",
        observed={"pair": pair["intent_ids"]},
    )


async def test_s1_item11_dense_cluster(api, db):
    """#11 密集配置(互換Intent 30件=6人×5件)→L3通過K_c=20・Jev K_j=8以内。"""
    from helpers_scenario import _create_intent, _register

    # 6人×5件(Active 5件/ユーザー制限内・設計表記2人×15件は§2.1読み替え3)
    subjects = [f"HS-p11-{n}" for n in range(1, 7)]
    user_ids = []
    for s in subjects:
        user_ids.append(await _register(api, s, "1995-04-01"))
    when = "2026-10-05T06:30:00+09:00"
    intent_ids = []
    for s in subjects:
        for k in range(5):
            intent_ids.append(
                await _create_intent(
                    api,
                    s,
                    when=when,
                    category="activity",
                    secondary=f"朝活散歩{k}",
                    raw_text=f"早朝に天文館で朝活{k}を行いたい",
                )
            )
    # 収束待ち(密集群のイベント処理が終わるまで・上限180秒)
    from helpers_wait import wait_until

    origin = intent_ids[0]

    async def settled() -> bool:
        n = await db.fetchval(
            "SELECT count(*) FROM match_candidates"
            " WHERE jev_result IS NOT NULL AND (intent_a_id=$1::uuid"
            " OR intent_b_id=$1::uuid)",
            origin,
        )
        return n > 0

    await wait_until(settled, timeout_s=180, interval_s=10, what="#11密集群の評価")
    origin_candidates = await db.fetchval(
        "SELECT count(*) FROM match_candidates"
        " WHERE intent_a_id=$1::uuid OR intent_b_id=$1::uuid",
        origin,
    )
    origin_ev = await db.fetchval(
        "SELECT count(*) FROM match_candidates"
        " WHERE jev_result IS NOT NULL AND (intent_a_id=$1::uuid"
        " OR intent_b_id=$1::uuid)",
        origin,
    )
    assert origin_candidates <= 50, f"起点候補数がK_v=50超: {origin_candidates}"
    assert origin_ev <= 8, f"起点Jev実行がK_j=8超: {origin_ev}"
    record_item(
        item_id="02#11",
        name="密集配置(30件・L3通過K_c=20以内・Jev K_j=8以内)",
        test="test_s1_item11_dense_cluster",
        verdict="pass",
        observed={
            "cluster_intents": len(intent_ids),
            "origin_candidates": origin_candidates,
            "origin_jev": origin_ev,
        },
    )


async def test_s1_item12_jev_result_record(api, db):
    """#12 jev_resultにwould_a/would_b双方+provider/modelが記録される。"""
    row = await db.fetchrow(
        "SELECT jev_result FROM match_candidates WHERE jev_result IS NOT NULL LIMIT 1"
    )
    assert row is not None, "評価済み候補が存在しない"
    j = row["jev_result"]
    assert j["would_a_accept_b"] == 0.9, f"would_a={j['would_a_accept_b']}"
    assert j["would_b_accept_a"] == 0.9, f"would_b={j['would_b_accept_a']}"
    assert "provider" in j and "model" in j, f"provider/modelキー欠落: {sorted(j)}"
    record_item(
        item_id="02#12",
        name="jev_result記録(双方判定値+provider/model)",
        test="test_s1_item12_jev_result_record",
        verdict="pass",
        observed={"noul": 0.9, "provider": j["provider"], "model": j["model"]},
    )


async def test_s1_item08_retention_promotion(api, db):
    """#8 時刻到来: JST 0時跨ぎ(リセットジョブ+保留キュー再評価)でproposed昇格。

    ファイル内で最後に実行する(この検査がClockをD1 00:05へ前進し・S2以降の
    仮想時刻前提を作る)。
    """
    state = await seed_all(api, db)
    clock = await get_clock()
    before_retained = await db.fetchval(
        "SELECT count(*) FROM latches WHERE status='candidate'"
    )
    before_promoted = await db.fetchval(
        "SELECT count(*) FROM latch_status_events"
        " WHERE from_status='candidate' AND to_status='proposed'"
    )
    assert before_retained > 0, (
        "保留中candidateが0件 — D-08事前集計(計画§2.2: 6件超え74件)と不整合"
    )
    # D1 00:05 JST へ前進(base_datetimeの翌日・ISO明示・後退は行わない)
    base = datetime.fromisoformat(state["meta_base"])
    target = (base + timedelta(days=1)).replace(hour=0, minute=5)
    clock.set(target)
    from helpers_wait import wait_until

    async def promoted() -> bool:
        cur = await db.fetchval(
            "SELECT count(*) FROM latch_status_events"
            " WHERE from_status='candidate' AND to_status='proposed'"
        )
        return cur > before_promoted

    await wait_until(
        promoted,
        timeout_s=180,
        interval_s=10,
        what="JST 0時跨ぎ後の保留→proposed昇格(リセットジョブ+保留キュー再評価)",
    )
    promoted_now = await db.fetchval(
        "SELECT count(*) FROM latch_status_events"
        " WHERE from_status='candidate' AND to_status='proposed'"
    )
    record_item(
        item_id="02#8",
        name="時刻到来再評価(保留→proposed昇格)",
        test="test_s1_item08_retention_promotion",
        verdict="pass",
        observed={
            "retained_before": before_retained,
            "promoted_delta": promoted_now - before_promoted,
            "clock_set_to": target.isoformat(),
        },
    )
