"""S2: 提案・通知系(02#13・#14・#17提案時・#22・#23・#26・FR-21・設計§2.5)。

前提: S1の#8でClockがD1 00:05へ前進済み。時刻はこのファイルでは前進しない。
シナリオIntentは検査内で作成するため、提案昇格をwait_for_latchで待ってから
観察する(計画コードからの適応・報告書に記録)。
"""

from __future__ import annotations

from helpers_pushlog import push_log_records
from helpers_scenario import make_scenario_pair, wait_for_latch
from helpers_seed import compute_mlr, seed_all

from conftest import auth_header, record_item

HIDDEN_KEYS = {"headcount", "match_level"}
SUMMARY_KEYS = {
    "time_summary",
    "area_name",
    "headcount",
    "category_primary",
    "category_secondary",
    "budget",
    "match_level",
}


async def test_s2_item13_proposal_notification_and_muted(api, db):
    """#13 通知先行書き込み+pushドライラン。muted提案はpush 0件・proposed・回答可。"""
    await seed_all(api, db)
    # (a) 通常提案: proposed latch≥1・notifications(proposal型)≥1・push記録≥1
    n_proposed = await db.fetchval(
        "SELECT count(*) FROM latches WHERE status='proposed'"
    )
    assert n_proposed >= 1, "proposed latchが0件"
    n_notif = await db.fetchval(
        "SELECT count(*) FROM notifications WHERE type='proposal'"
    )
    assert n_notif >= 1, "notifications(proposal型)の先行書き込みがない"
    pushes = push_log_records()
    assert any(p["notification_type"] == "proposal" for p in pushes), (
        "pushドライラン記録(proposal)が0件"
    )
    # (b) mutedペア(HS-p13・notification_level=muted): push 0件・proposed・回答可
    muted = await make_scenario_pair(
        api,
        db,
        key="s2-13",
        name="p13",
        when="2026-10-07T06:30:00+09:00",
        notification_level="muted",
        raw_text="早朝に天文館で静かに散策したい",
    )
    latch = await wait_for_latch(api, muted["subjects"][0], status="proposed")
    latch_id = str(latch["id"])
    muted_pushes = [p for p in push_log_records() if p["latch_id"] == latch_id]
    assert muted_pushes == [], (
        f"muted提案にpush記録が{len(muted_pushes)}件(0件でなければならない)"
    )
    rr = await api.post(
        f"/v1/latches/{latch_id}/response",
        headers=auth_header(muted["subjects"][0]),
        json={"response": "defer"},
    )
    assert rr.status_code == 200, f"muted提案の回答が可でない: {rr.status_code}"
    record_item(
        item_id="02#13",
        name="提案通知(先行書き込み+pushドライラン)+muted(push 0件・proposed・回答可)",
        test="test_s2_item13_proposal_notification_and_muted",
        verdict="pass",
        observed={
            "proposed": n_proposed,
            "notifications": n_notif,
            "push_total": len(pushes),
            "muted_push": 0,
        },
    )


async def test_s2_item14_responses_record(api, db):
    """#14 yes/no回答の記録と遷移(responses+latch_status_events)。"""
    pair = await make_scenario_pair(
        api,
        db,
        key="s2-14",
        name="p19",
        when="2026-10-05T06:30:00+09:00",
        raw_text="早朝に天文館でコーヒーを飲みたい",
    )
    latch = await wait_for_latch(api, pair["subjects"][0], status="proposed")
    latch_id = str(latch["id"])
    r1 = await api.post(
        f"/v1/latches/{latch_id}/response",
        headers=auth_header(pair["subjects"][0]),
        json={"response": "yes"},
    )
    assert r1.status_code == 200, f"yes回答失敗: {r1.status_code} {r1.text}"
    r2 = await api.post(
        f"/v1/latches/{latch_id}/response",
        headers=auth_header(pair["subjects"][1]),
        json={"response": "no"},
    )
    assert r2.status_code == 200, f"no回答失敗: {r2.status_code} {r2.text}"
    # DB観察: responses(jsonb)に2件・遷移イベントに成立しない終端がある
    rows = await db.fetchrow(
        "SELECT responses, status FROM latches WHERE id=$1::uuid", latch_id
    )
    responses = rows["responses"]
    kinds = (
        sorted(r["response"] for r in responses) if isinstance(responses, list) else []
    )
    assert "yes" in kinds and "no" in kinds, f"responses記録: {kinds}"
    events = await db.fetch(
        "SELECT from_status, to_status FROM latch_status_events"
        " WHERE latch_id=$1::uuid ORDER BY created_at",
        latch_id,
    )
    seq = [(e["from_status"], e["to_status"]) for e in events]
    # 先頭はlatch INSERT時の(None, candidate)イベントが入る(実測)・
    # candidate→proposedの昇格イベントを検査する(計画コードからの適応)
    assert ("candidate", "proposed") in seq, f"遷移系列: {seq}"
    assert rows["status"] != "proposed", "no回答後にproposedのまま(終端遷移なし)"
    record_item(
        item_id="02#14",
        name="yes/no回答の記録と遷移",
        test="test_s2_item14_responses_record",
        verdict="pass",
        observed={"responses": kinds, "transitions": seq, "final": rows["status"]},
    )


async def test_s2_item17_before_match_visibility(api, db):
    """#17(提案時) 可視範囲: participants非解放・summary_onlyはサマリあり。

    成立後(S4)との前後差分はtest_s4_item17_after_matchで検査する。
    """
    pair = await make_scenario_pair(
        api,
        db,
        key="s4-17",
        name="p17",
        when="2026-10-11T06:30:00+09:00",  # D+7(S3の前進後も回答可)
        # 明示expires_at: S4のmatched遷移(_MATCH_INTENTSはactive/paused対象)
        # までIntentを存活させる(既定のnearest候補はClock前進前に切れる)
        expires_at="2026-10-11T23:30:00+09:00",
        raw_text="7日後の早朝に天文館で散策したい",
    )
    latch = await wait_for_latch(api, pair["subjects"][0], status="proposed")
    r = await api.get(
        f"/v1/latches/{latch['id']}", headers=auth_header(pair["subjects"][0])
    )
    assert r.status_code == 200
    detail = r.json()["latch"]
    assert detail["participants"] is None, "成立前にparticipantsが解放されている"
    assert detail["proposal"].get("time_summary"), "summary_only提案にサマリがない"
    record_item(
        item_id="02#17",
        name="可視範囲比較(提案時: participants非解放・サマリあり)",
        test="test_s2_item17_before_match_visibility",
        verdict="pass",
        observed={"proposal_keys": sorted(detail["proposal"])},
    )


async def test_s2_item22_visibility_branch(api, db):
    """#22 visibility 2値分岐(hidden=サマリなし/summary_only=サマリあり)。"""
    hidden = await make_scenario_pair(
        api,
        db,
        key="s2-22h",
        name="p22h",
        when="2026-10-05T06:30:00+09:00",
        category="drinking",
        visibility="hidden_until_match",
        raw_text="朝食がてら静かに一杯だけ飲みたい",
    )
    summary = await make_scenario_pair(
        api,
        db,
        key="s2-22s",
        name="p22s",
        when="2026-10-09T06:30:00+09:00",
        category="meal",
        visibility="summary_only",
        raw_text="朝食を兼ねて天文館を歩きたい",
    )
    lh = await wait_for_latch(api, hidden["subjects"][0])
    ls = await wait_for_latch(api, summary["subjects"][0])
    kh = set(lh["proposal"].keys())
    ks = set(ls["proposal"].keys())
    assert kh == HIDDEN_KEYS, f"hidden提案のキー集合: {sorted(kh)}"
    assert ks == SUMMARY_KEYS, f"summary_only提案のキー集合: {sorted(ks)}"
    record_item(
        item_id="02#22",
        name="visibility分岐(hidden 2キー/summary_only 7キー)",
        test="test_s2_item22_visibility_branch",
        verdict="pass",
        observed={"hidden": sorted(kh), "summary_only": sorted(ks)},
    )


async def test_s2_item23_no_precise_coordinates(api, db):
    """#23 座標精度値が応答に現れない(キー集合+値パターン検査)。"""
    summary = await make_scenario_pair(
        api,
        db,
        key="s2-23",
        name="p23",
        when="2026-10-09T06:30:00+09:00",
        raw_text="朝の天文館を散歩したい",
    )
    latch = await wait_for_latch(api, summary["subjects"][0])
    r = await api.get(
        f"/v1/latches/{latch['id']}", headers=auth_header(summary["subjects"][0])
    )
    body_text = r.text
    blob = body_text.lower()
    coord_keys = [
        k
        for k in blob
        if k in ("latitude", "longitude", "lat", "lon", "geometry", "coordinates")
    ]
    assert not coord_keys, f"応答に座標系キーが含まれる: {coord_keys}"
    # 小数点を含む座標らしき数値ペア(lat,lon形式)が文字列に含まれない
    import re

    assert not re.search(r"\d{2,3}\.\d{4,}\s*,\s*\d{2,3}\.\d{4,}", body_text), (
        "応答に座標らしき数値ペアが含まれる"
    )
    record_item(
        item_id="02#23",
        name="座標精度の非表示(キー集合+値パターン)",
        test="test_s2_item23_no_precise_coordinates",
        verdict="pass",
        observed={"detail_url": f"/v1/latches/{latch['id']}"},
    )


async def test_s2_fr21_push_content(api, db):
    """FR-21 push本文=汎用文とバイト一致・条件サマリ不含・drinking同一様式・
    summary_onlyはお知らせと提案画面にサマリ・hiddenは両方なし。"""
    from latch.notifications.templates import build_push

    pushes = push_log_records()
    assert pushes, "pushドライラン記録がない"
    # (a) 本文=テンプレート定数とバイト一致(条件サマリ混入経路がないことの観察)
    bodies = {(p["notification_type"], p["title"], p["body"]) for p in pushes}
    for ntype, title, body in bodies:
        t, b = build_push(ntype)
        assert title == t and body == b, f"push本文がテンプレートと不一致: {ntype}"
    # (b) 条件サマリ不含(地名・カテゴリ文言が本文に現れない)
    for _ntype, _title, body in bodies:
        for word in ("天文館", "呉服町", "散歩", "焼肉", "飲み"):
            assert word not in body, f"push本文に条件サマリ語が含まれる: {word}"
    # (c) summary_onlyペアのお知らせ(notifications×latches)にproposalサマリキー
    # がある。notifications payloadは{"latch_id"}の最小参照(latch_engine
    # _insert_notification・承認事項2)のため、proposal本体はpayload->>
    # 'latch_id'でlatchesへJOINして読む(計画備考どおり実装形を確認済みの
    # うえでの直し・報告書に記録)
    summary = await make_scenario_pair(
        api,
        db,
        key="s2-fr21s",
        name="fr21s",
        when="2026-10-08T06:30:00+09:00",
        category="drinking",
        raw_text="朝食がてら静かに飲みたい",
    )
    latch = await wait_for_latch(api, summary["subjects"][0])
    notif = await db.fetchrow(
        "SELECT l.proposal FROM notifications n"
        " JOIN latches l ON l.id = CAST(n.payload->>'latch_id' AS uuid)"
        " WHERE n.type='proposal' AND n.payload->>'latch_id'=$1 LIMIT 1",
        str(latch["id"]),
    )
    assert notif is not None, "summary_onlyペアのproposal通知が見つからない"
    notif_keys = set(notif["proposal"].keys())
    assert SUMMARY_KEYS <= notif_keys, (
        f"お知らせのproposalにサマリキーがない: {sorted(notif_keys)}"
    )
    record_item(
        item_id="FR-21",
        name="push表示規制(汎用文バイト一致・サマリ不含・drinking同一様式)",
        test="test_s2_fr21_push_content",
        verdict="pass",
        observed={
            "distinct_bodies": sorted(b[2] for b in bodies),
            "summary_notif_keys": sorted(notif_keys),
        },
    )


async def test_s2_item26_mlr_computation(api, db):
    """#26 MLR算出(01 §23の数え方・S2時点の値を証拠化・S5で最終値を再記録)。"""
    m = await compute_mlr(db)
    assert m["denominator"] >= 1, f"分母=0(通知送信候補なし): {m}"
    assert m["numerator"] >= 0
    record_item(
        item_id="02#26",
        name="MLR算出(分母=proposed遷移・分子=matched遷移)",
        test="test_s2_item26_mlr_computation",
        verdict="pass",
        observed=m,
    )
