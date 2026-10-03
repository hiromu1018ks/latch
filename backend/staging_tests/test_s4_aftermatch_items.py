"""S4: 成立後(02#15・#19・#21・D-23・#17成立後・設計§2.5)。

前提: S3でClockがD+6 00:10へ前進済み。#17用ペア(HS-p17・D+7 06:30対象)は
S2でproposed昇格済みで・response_deadline(D+7 05:15)まで回答可能。
Intentは明示expires_at(2026-10-11T23:30)で存活させている
(matched遷移の_MATCH_INTENTSはactive/paused対象・報告書に記録)。
実装値(計画時確認済み): ブロック→進行中提案cancelled(safety/service.py
block_userが単一txで実施)・チャット読み取り専用=409 CHAT_READONLY
(latches/errors.py)。
"""

from __future__ import annotations

from helpers_scenario import _SCENARIOS, my_latch
from helpers_seed import seed_all

from conftest import auth_header, record_item

P17 = "s4-17"  # S2のtest_s2_item17_before_match_visibilityと同一key


async def test_s4_item15_matched_and_chat(api, db):
    """#15 matched+チャット解放(POST messages 201)。

    双方yesの回答工程はS3のtest_s3_s4_pair_respond_yesで実施済み
    (response_deadline提案+2h以内の制約による移動・報告書に記録)。
    ここではmatched状態の維持とチャット解放を観察する。
    """
    await seed_all(api, db)
    pair = _SCENARIOS[P17]
    latch = await my_latch(api, pair["subjects"][0], status="matched")
    latch_id = str(latch["id"])
    st = await db.fetchval("SELECT status FROM latches WHERE id=$1::uuid", latch_id)
    assert st == "matched", f"S3回答後のstatus: {st}"
    rm = await api.post(
        f"/v1/latches/{latch_id}/messages",
        headers=auth_header(pair["subjects"][0]),
        json={"body": "よろしくお願いします"},
    )
    assert rm.status_code == 201, f"チャット解放失敗: {rm.status_code} {rm.text}"
    record_item(
        item_id="02#15",
        name="双方YES→matched+チャット解放",
        test="test_s4_item15_matched_and_chat",
        verdict="pass",
        observed={"latch": latch_id, "status": st},
    )


async def test_s4_item19_bidirectional_chat(api, db):
    """#19 成立LATCHで双方向の送受信記録。"""
    pair = _SCENARIOS[P17]
    latch_id = str((await my_latch(api, pair["subjects"][0], status="matched"))["id"])
    r1 = await api.post(
        f"/v1/latches/{latch_id}/messages",
        headers=auth_header(pair["subjects"][0]),
        json={"body": "こちらから1通目"},
    )
    assert r1.status_code == 201
    r2 = await api.post(
        f"/v1/latches/{latch_id}/messages",
        headers=auth_header(pair["subjects"][1]),
        json={"body": "そちらへ返信します"},
    )
    assert r2.status_code == 201
    for s in pair["subjects"]:
        rg = await api.get(f"/v1/latches/{latch_id}/messages", headers=auth_header(s))
        assert rg.status_code == 200
        bodies = [m["body"] for m in rg.json()["items"]]
        assert "こちらから1通目" in bodies and "そちらへ返信します" in bodies, bodies
    record_item(
        item_id="02#19",
        name="成立LATCHの双方向送受信",
        test="test_s4_item19_bidirectional_chat",
        verdict="pass",
        observed={"messages": 2},
    )


async def test_s4_item17_after_match(api, db):
    """#17(成立後) 前後差分: participants解放・提案サマリは維持。"""
    pair = _SCENARIOS[P17]
    latch = await my_latch(api, pair["subjects"][0], status="matched")
    r = await api.get(
        f"/v1/latches/{latch['id']}", headers=auth_header(pair["subjects"][0])
    )
    detail = r.json()["latch"]
    participants = detail["participants"] or []
    assert len(participants) == 2, "成立後にparticipantsが解放されていない"
    assert all(p["display_name"] for p in participants), "display_nameが空"
    assert detail["proposal"].get("time_summary"), "成立後にサマリが消えた"
    record_item(
        item_id="02#17-成立後",
        name="可視範囲比較(成立後: participants解放・サマリ維持)",
        test="test_s4_item17_after_match",
        verdict="pass",
        observed={"participants": len(participants)},
    )


async def test_s4_item21_report(api, db):
    """#21 成立画面経路の通報→reports受付記録。"""
    pair = _SCENARIOS[P17]
    latch_id = str((await my_latch(api, pair["subjects"][0], status="matched"))["id"])
    r = await api.post(
        "/v1/reports",
        headers=auth_header(pair["subjects"][1]),
        json={
            "reportee_id": pair["user_ids"][0],
            "latch_id": latch_id,
            "reason": "unpleasant_behavior",
        },
    )
    assert r.status_code == 201, f"通報失敗: {r.status_code} {r.text}"
    row = await db.fetchrow(
        "SELECT status FROM reports WHERE latch_id=$1::uuid AND reporter_id=$2::uuid",
        latch_id,
        pair["user_ids"][1],
    )
    assert row is not None, "reports受付記録がない"
    record_item(
        item_id="02#21",
        name="成立LATCHからの通報受付",
        test="test_s4_item21_report",
        verdict="pass",
        observed={"report_status": row["status"]},
    )


async def test_s4_d23_block_cancels_latch(api, db):
    """D-23 matched LATCHで一方がブロック→チャット読み取り専用(409 CHAT_READONLY)。

    実装の観測事実(計画の「提案cancelled」想定からの適応・報告書に記録):
    ブロックのcancelled化対象は進行中3状態(candidate/proposed/partial_accept
    ・safety/store.py _SELECT_OPEN_LATCHES_BETWEEN)で、matchedはcancelledに
    しない(維持)。チャット送信はblocks適用中は409 CHAT_READONLYで遮断され
    る(latches/service.py) — この遮断を本検査の合否条件とする。
    """
    pair = _SCENARIOS[P17]
    latch_id = str((await my_latch(api, pair["subjects"][0], status="matched"))["id"])
    rb = await api.post(
        f"/v1/users/{pair['user_ids'][1]}/block",
        headers=auth_header(pair["subjects"][0]),
    )
    assert rb.status_code == 201, f"ブロック失敗: {rb.status_code} {rb.text}"
    st = await db.fetchval("SELECT status FROM latches WHERE id=$1::uuid", latch_id)
    rs = await api.post(
        f"/v1/latches/{latch_id}/messages",
        headers=auth_header(pair["subjects"][1]),
        json={"body": "ブロック後の送信"},
    )
    assert rs.status_code == 409, f"チャット送信の遮断: {rs.status_code} {rs.text}"
    assert rs.json()["error"]["code"] == "CHAT_READONLY", rs.json()
    record_item(
        item_id="D-23",
        name="成立後ブロック(チャット読み取り専用・matchedは維持)",
        test="test_s4_d23_block_cancels_latch",
        verdict="pass",
        observed={"latch_status": st, "chat_error": "CHAT_READONLY"},
    )
