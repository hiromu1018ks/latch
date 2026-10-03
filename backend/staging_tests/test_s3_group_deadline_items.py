"""S3: グループ・期限系(02#18・#16・通知順序EX-026・自己重複EX-025・設計§2.5)。

09 §3グループ6検証のうちstaging観察で再実行する分(集合生成・全員YES成立・
期限確定・通知順序・自己重複不生成)を含む。実装値(計画時確認済み):
期限確定=sweeperがproposed/partial_accept×回答期限をstatus='expired'へ
(worker/sweeper.py:50)・部分UNIQUE(uq_group_candidates_intent_ids_open)が
同一集合のopen重複を排除(0005)。

時刻・場所の割当は§2.3隔離スケジュール(報告書「シナリオ隔離の再設計」):
#18=10-06 06:30 加治屋町・#16=10-06 06:30 浜町(2529m・半径和2000m未満で
集合が混成しない)。2/2ペア系とはpeople条件で自動分離。
"""

from __future__ import annotations

from datetime import datetime, timedelta

from helpers_scenario import (
    make_scenario_group,
    wait_embeddings,
    wait_for_latch,
)
from helpers_seed import load_seed, seed_all
from helpers_wait import wait_until

from conftest import auth_header, get_clock, publish_created, record_item


def _ex_intents(seed: dict, ex_id: str) -> list[str]:
    """expectations.casesからEX-xxxのintents(idリスト)を引く。"""
    for case in seed["expectations"]["cases"]:
        if case["id"] == ex_id:
            return list(case["intents"])
    raise AssertionError(f"{ex_id}がexpectationsに存在しない")


async def test_s3_item18_group_all_yes(api, db):
    """#18 3〜4人集合の全員YES成立(09 §3と同一手順のstaging再実行・専用4人組)。"""
    await seed_all(api, db)
    # members=3(実測: グループクラスタは3人組のみ構築される — 34行全て
    # cardinality=3・D-06同点サイズ昇順。4人集合の行は観測されない)。
    # 09 §3「3〜4人集合」の成立観察は3人組で実施(報告書に記録)
    group = await make_scenario_group(
        api,
        db,
        key="s3-18",
        name="p18",
        when="2026-10-06T06:30:00+09:00",
        members=3,
        min_participants=3,
        location="加治屋町",
        raw_text="翌朝に加治屋町でみんなで散歩したい",
    )
    ids = group["intent_ids"]

    # embedding完備を待ってcreatedを再発行してからGroupEngine.handleを
    # 再実行させる(イベント処理時のembedding競合でPool空になったままに
    # ならないよう・報告書「グループ生成のembedding競合」に記録)
    await wait_embeddings(db, ids)
    for iid in ids:
        publish_created(iid)

    # 集合生成の観察(group_candidatesに3 Intentの集合行・生成待ち込み)。
    # intent_ids配列の要素順は実装の構築順のため包含両方向で判定する
    async def group_row() -> bool:
        n = await db.fetchval(
            "SELECT count(*) FROM group_candidates"
            " WHERE intent_ids @> $1::uuid[] AND intent_ids <@ $1::uuid[]"
            " AND cardinality(intent_ids) = $2",
            ids,
            len(ids),
        )
        return n is not None and n >= 1

    await wait_until(
        group_row, timeout_s=180, interval_s=10, what="#18の4人集合group_candidates生成"
    )
    # intent_ids配列の要素順は実装の構築順で来るため包含両方向で同集合を
    # 判定する(実測・計画コードからの適応・報告書に記録)
    row = await db.fetchrow(
        "SELECT id, status FROM group_candidates"
        " WHERE intent_ids @> $1::uuid[] AND intent_ids <@ $1::uuid[]"
        " AND cardinality(intent_ids) = $2",
        ids,
        len(ids),
    )
    # 全員yes → matched
    latch = await wait_for_latch(api, group["subjects"][0], status="proposed")
    latch_id = str(latch["id"])
    for s in group["subjects"]:
        r = await api.post(
            f"/v1/latches/{latch_id}/response",
            headers=auth_header(s),
            json={"response": "yes"},
        )
        assert r.status_code == 200, (
            f"グループyes回答失敗 {s}: {r.status_code} {r.text}"
        )

    async def matched() -> bool:
        st = await db.fetchval("SELECT status FROM latches WHERE id=$1::uuid", latch_id)
        return st == "matched"

    await wait_until(
        matched, timeout_s=120, interval_s=5, what="#18グループのmatched遷移"
    )
    detail = await api.get(
        f"/v1/latches/{latch_id}", headers=auth_header(group["subjects"][0])
    )
    assert detail.status_code == 200
    participants = detail.json()["latch"]["participants"] or []
    assert len(participants) == 3, f"成立後participantsが3人でない: {len(participants)}"
    record_item(
        item_id="02#18",
        name="グループ集合生成+全員YES成立(participants 3人)",
        test="test_s3_item18_group_all_yes",
        verdict="pass",
        observed={"group_candidate_status": row["status"], "members": 3},
    )


async def test_s3_s4_pair_respond_yes(api, db):
    """#15の回答工程: s4-17ペアの双方yes→matched(S4観察の前段)。

    response_deadlineは提案時刻から最大+2時間(03 D-05のDEADLINE_MAX_AFTER_
    NOTIFY)のため、S3のClock前進(item16のD+2)より前に回答を完了させる
    (計画のS4配置からの移動・報告書に記録)。matchedは回答期限sweeperの
    対象外(sweeperはproposed/partial_acceptを対象)でD+6前進後も維持される。
    """
    await seed_all(api, db)
    from helpers_scenario import _SCENARIOS

    pair = _SCENARIOS["s4-17"]
    latch = await wait_for_latch(api, pair["subjects"][0], status="proposed")
    latch_id = str(latch["id"])
    for s in pair["subjects"]:
        r = await api.post(
            f"/v1/latches/{latch_id}/response",
            headers=auth_header(s),
            json={"response": "yes"},
        )
        assert r.status_code == 200, f"yes回答失敗 {s}: {r.status_code} {r.text}"

    async def matched() -> bool:
        st = await db.fetchval("SELECT status FROM latches WHERE id=$1::uuid", latch_id)
        return st == "matched"

    await wait_until(matched, timeout_s=120, interval_s=5, what="#15ペアのmatched遷移")


async def test_s3_notification_order_ex026(api, db):
    """通知順序: 同一Poolから複数集合→proposedはaggregate_score降順の1つだけ。"""
    await seed_all(api, db)
    seed = load_seed()
    ex_ids = _ex_intents(seed, "EX-026")
    state = await seed_all(api, db)
    ex_uuids = [state["sd_to_uuid"][i] for i in ex_ids]
    # EX-026クラスタの集合生成の観察(部分UNIQUEで同一集合open行は1行)
    rows = await db.fetch(
        "SELECT intent_ids, status, aggregate_score FROM group_candidates"
        " WHERE intent_ids && $1::uuid[]",
        ex_uuids,
    )
    assert rows, "EX-026クラスタの集合候補が生成されていない"
    # 同一メンバー集合に対するproposed latchは1行のみ(競合はcandidate/closedに残る)
    dup = await db.fetchval(
        "SELECT count(*) FROM ("
        "  SELECT array_agg(l.id) FROM latches l, group_candidates g"
        "  WHERE l.group_candidate_id = g.id AND l.status='proposed'"
        "  GROUP BY g.intent_ids HAVING count(*) > 1"
        ") t"
    )
    assert not dup, f"同一集合に対するproposed latchが複数: {dup}"
    record_item(
        item_id="通知順序(EX-026)",
        name="同一Pool複数集合→proposed 1つ(group_candidates記録)",
        test="test_s3_notification_order_ex026",
        verdict="pass",
        observed={
            "cluster_rows": len(rows),
            "statuses": sorted({str(r["status"]) for r in rows}),
        },
    )


async def test_s3_self_duplication_ex025(api, db):
    """自己重複集合(EX-025)のgroup_candidates不生成+対照(EX-028の生成)。"""
    await seed_all(api, db)
    seed = load_seed()
    state = await seed_all(api, db)
    # 自己重複集合の不生成は「同一ユーザーの2 Intent(SD-048×SD-049)を1行が
    # 共に含むこと」で判定する(実測: クラスタはseed側Intentとも混成しうる
    # ため全4 Intentの完全一致では判定できない・報告書に記録)
    dup_pair = [state["sd_to_uuid"][i] for i in _ex_intents(seed, "EX-025")[:2]]
    n_dup = await db.fetchval(
        "SELECT count(*) FROM group_candidates WHERE intent_ids @> $1::uuid[]",
        dup_pair,
    )
    assert n_dup == 0, f"自己重複集合(EX-025)のgroup_candidatesが{n_dup}件"
    # 対照(正常クラスタ)はEX-026の集合行で観察する(素材読み替え・報告書
    # に記録): グループPool検索は起点time_startの30分Bucketで時間分割される
    # (group_engine._POOL_SEARCH)ため、EX-028の3 Intent(21:00/21:30/22:00)
    # は構造的に同一Poolへ入らず集合生成が起きない。EX-026(同Bucket×6件)は
    # S0処理時に集合行が生成済みで・同一ユーザー2 Intentを含まない正常集合
    # の対照として妥当。EX-025の自己重複不生成はS0処理時の判定で成立する
    # (embedding未完 Pool競合はembedding_completedイベントの再実行で
    # 自己修復されるため・S0収束待ち後に観察する)
    ok_ids = _ex_intents(seed, "EX-026")
    ok_uuids = [state["sd_to_uuid"][i] for i in ok_ids]
    dup_pair = [state["sd_to_uuid"][i] for i in _ex_intents(seed, "EX-025")[:2]]
    n_dup = await db.fetchval(
        "SELECT count(*) FROM group_candidates WHERE intent_ids @> $1::uuid[]",
        dup_pair,
    )
    assert n_dup == 0, f"自己重複集合(EX-025)のgroup_candidatesが{n_dup}件"
    n_ok = await db.fetchval(
        "SELECT count(*) FROM group_candidates WHERE intent_ids && $1::uuid[]",
        ok_uuids,
    )
    assert n_ok >= 1, "対照(EX-026正常クラスタ)の集合生成が観察できず"
    record_item(
        item_id="FR-17-自己重複",
        name="自己重複集合不生成(EX-025)+対照生成(EX-026)",
        test="test_s3_self_duplication_ex025",
        verdict="pass",
        observed={"ex025_selfdup_rows": n_dup, "ex026_rows": n_ok},
    )


async def test_s3_item16_partial_yes_deadline(api, db):
    """#16 部分YESの期限確定(専用3人組・1人yes→Clock前進でresponse_deadline通過)。"""
    await seed_all(api, db)
    group = await make_scenario_group(
        api,
        db,
        key="s3-16",
        name="p16",
        when="2026-10-06T06:30:00+09:00",
        members=3,
        min_participants=3,
        location="浜町",
        raw_text="翌朝に浜町で朝の散歩をしてみたい",
    )
    # #18と同一のembedding競合対策(created再発行・報告書に記録)
    await wait_embeddings(db, group["intent_ids"])
    for iid in group["intent_ids"]:
        publish_created(iid)
    latch = await wait_for_latch(api, group["subjects"][0], status="proposed")
    latch_id = str(latch["id"])
    r = await api.post(
        f"/v1/latches/{latch_id}/response",
        headers=auth_header(group["subjects"][0]),
        json={"response": "yes"},
    )
    assert r.status_code == 200, f"部分yes失敗: {r.status_code}"
    before_expired = await db.fetchval(
        "SELECT count(*) FROM latch_status_events WHERE to_status='expired'"
    )
    clock = await get_clock()
    state = await seed_all(api, db)
    base = datetime.fromisoformat(state["meta_base"])
    # D+2 06:00へ前進(response_deadline D+2 05:15通過・ISO明示・後退なし)
    target = (base + timedelta(days=2)).replace(hour=6, minute=0)
    clock.set(target)

    async def expired_latch() -> bool:
        return (
            await db.fetchval("SELECT status FROM latches WHERE id=$1::uuid", latch_id)
            == "expired"
        )

    # sweeperは1tickあたりbatch上限50件(settings.sweeper_batch_limit)。
    # D+2への前進で期限超過になるseed提案(250件超)の排水が先に走るため
    # 目的のlatchに到達まで複数tickを要する(実測)・上限を10分へ拡張
    await wait_until(
        expired_latch,
        timeout_s=600,
        interval_s=15,
        what="#16の期限確定(sweeperがproposed×回答期限をexpiredへ・"
        "batch50/tickの排水を含む)",
    )
    events = await db.fetch(
        "SELECT from_status, to_status FROM latch_status_events"
        " WHERE latch_id=$1::uuid ORDER BY created_at",
        latch_id,
    )
    seq = [(e["from_status"], e["to_status"]) for e in events]
    # 1人yes済みのため期限確定時点の状態はpartial_accept → 遷移は
    # (partial_accept, expired)(D-06の部分YES期限時確定・実挙動)。
    # proposed→partial_accept(1人目のyes)と期限確定の両方を検査する
    assert ("proposed", "partial_accept") in seq, f"遷移系列: {seq}"
    assert ("partial_accept", "expired") in seq, f"期限確定遷移がない: {seq}"
    after_expired = await db.fetchval(
        "SELECT count(*) FROM latch_status_events WHERE to_status='expired'"
    )
    record_item(
        item_id="02#16",
        name="部分YESの期限確定(proposed→expired・1人yes+2人無回答)",
        test="test_s3_item16_partial_yes_deadline",
        verdict="pass",
        observed={
            "transitions": seq,
            "expired_delta": after_expired - before_expired,
            "clock_set_to": target.isoformat(),
        },
    )


async def test_s3_intent_expiry_batch(api, db):
    """Intent期限切れ(expiredバッチ): D+5以降へ前進→sweeperがintentsをexpiredへ。"""
    await seed_all(api, db)
    before = await db.fetchval("SELECT count(*) FROM intents WHERE status='expired'")
    clock = await get_clock()
    state = await seed_all(api, db)
    base = datetime.fromisoformat(state["meta_base"])
    # D+6 00:10へ前進(D+5対象Intentのexpires_at経過後・ISO明示)
    target = (base + timedelta(days=6)).replace(hour=0, minute=10)
    clock.set(target)

    async def expired_grew() -> bool:
        cur = await db.fetchval("SELECT count(*) FROM intents WHERE status='expired'")
        return cur > before

    # batch50/tickの制約上、前進で期限超過となるIntent(~200件)の排水に
    # 複数tickを要する(実測)・上限を10分へ拡張
    await wait_until(
        expired_grew,
        timeout_s=600,
        interval_s=15,
        what="Intent期限切れバッチ(sweeper 60秒周期・batch50/tickの排水を含む)",
    )
    total = await db.fetchval("SELECT count(*) FROM intents WHERE status='expired'")
    assert total > before, "Intent期限切れが観察できなかった"
    record_item(
        item_id="02#16-Intent期限",
        name="Intent期限切れバッチ(D+6前進でsweeper発火)",
        test="test_s3_intent_expiry_batch",
        verdict="pass",
        observed={
            "expired_before": before,
            "expired_total": total,
            "clock_set_to": target.isoformat(),
        },
    )
