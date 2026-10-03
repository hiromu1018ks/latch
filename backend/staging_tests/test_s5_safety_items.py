"""S5: 安全系(02#20・D-10・#26最終集計・設計§2.5)。

intended_reject_cases(EX-029〜033)は投入せず・ここで422 UNDER_AGE試行素材
として使う(設計§3.1・ws-2引継ぎ)。422応答形式は{"error": {"code": ...}}
(main.py _error_body)。
"""

from __future__ import annotations

from helpers_scenario import _SCENARIOS, _create_intent
from helpers_seed import (
    compute_mlr,
    ex_pair_ids,
    fetch_candidates_pairs,
    load_seed,
    seed_all,
)
from helpers_wait import wait_until

from conftest import auth_header, record_item


async def test_s5_item20_block_prevents_candidates(api, db):
    """#20 ブロック設定後に一致Intentを配置→候補生成されない。"""
    await seed_all(api, db)
    pair = _SCENARIOS["s5-20"]
    # 171→172をブロック(171のIntentはS0収束待ちの中で作成済み・明示
    # expires_atでS5時点でもactive)
    rb = await api.post(
        f"/v1/users/{pair['user_ids'][1]}/block",
        headers=auth_header(pair["subjects"][0]),
    )
    assert rb.status_code == 201, f"ブロック失敗: {rb.status_code}"
    # 172のIntent(171と一致条件: 同一日時・同一地点・同一カテゴリ)を作成
    before = await db.fetchval(
        "SELECT count(*) FROM match_events"
        " WHERE event_type='created' AND status='processed'"
    )
    new_id = await _create_intent(
        api,
        pair["subjects"][1],
        when="2026-10-11T06:30:00+09:00",
        category="drinking",
        secondary="朝の一杯",
        raw_text="早朝に天文館で静かに一杯だけ飲みたい",
    )

    async def processed() -> bool:
        cur = await db.fetchval(
            "SELECT count(*) FROM match_events"
            " WHERE event_type='created' AND status='processed'"
        )
        return cur > before

    await wait_until(processed, timeout_s=180, interval_s=10, what="#20新Intentの処理")
    import asyncio

    await asyncio.sleep(70)  # 後続層(debounce+評価)の完了猶予
    n = await db.fetchval(
        "SELECT count(*) FROM match_candidates"
        " WHERE (intent_a_id=$1::uuid AND intent_b_id=$2::uuid)"
        " OR (intent_a_id=$2::uuid AND intent_b_id=$1::uuid)",
        pair["intent_ids"][0],
        new_id,
    )
    assert n == 0, f"ブロック後の一致Intentで候補が生成された: {n}"
    record_item(
        item_id="02#20",
        name="ブロック後の一致Intentは候補生成されない",
        test="test_s5_item20_block_prevents_candidates",
        verdict="pass",
        observed={"blocked_pair_candidates": n},
    )


async def test_s5_d10_under_age(api, db):
    """D-10(#27動作面): alcohol Intentは422 UNDER_AGE+EX-004/005非生成。"""
    await seed_all(api, db)
    seed = load_seed()
    state = await seed_all(api, db)
    results = []
    for case in seed["intended_reject_cases"]:
        # time.startはYAML生成時のD+1..D+5のままだとS5時点(仮想D+6)で過去に
        # なり422 VALIDATION_ERROR(過去不可)が先に発火するため、年齢検証へ
        # 到達させる未来時刻へ差し替える(検査意味は不変・報告書に記録)
        si = {
            **case["structured_intent"],
            "time": {
                **case["structured_intent"]["time"],
                "start": "2026-10-11T20:00:00+09:00",
            },
        }
        r = await api.post(
            "/v1/intents",
            headers=auth_header(case["user"]),
            json={
                "raw_text": case["raw_text"],
                "status": "active",
                "structured_intent": si,
            },
        )
        assert r.status_code == 422, (
            f"{case['id']}: 期待422・実際{r.status_code} {r.text}"
        )
        code = r.json()["error"]["code"]
        assert code == case["expected_code"], f"{case['id']}: code={code}"
        results.append({"id": case["id"], "code": code})
    # EX-004/005(age_alcohol)はLayer 1で除外済み(S0突合と同値をD-10として明示記録)
    db_pairs = await fetch_candidates_pairs(db, state["sd_to_uuid"])
    for ex in ("EX-004", "EX-005"):
        assert ex_pair_ids(seed, ex) not in db_pairs, f"{ex}が候補に存在"
    record_item(
        item_id="D-10",
        name="年齢制限(422 UNDER_AGE 5件+EX-004/005非生成)",
        test="test_s5_d10_under_age",
        verdict="pass",
        observed={"rejects": results},
    )


async def test_s5_item26_final_mlr(api, db):
    """#26(最終) 全シーン終了時点のMLR(S2の中間集計との差分も記録)。"""
    await seed_all(api, db)
    m = await compute_mlr(db)
    assert m["denominator"] >= 1
    record_item(
        item_id="02#26-最終",
        name="MLR最終集計(全シーン終了時点)",
        test="test_s5_item26_final_mlr",
        verdict="pass",
        observed=m,
    )
