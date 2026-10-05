"""n5: Queue障害(10 §4.7)。毒ペイロード・参照不在・UNIQUE重複・非波及・lag alert。

実行前提: staging-nfr-reset直後のフル起動(lag alert有効)+ci一時停止。
worker停止を伴う試験は必ずtry/finallyでworkers_start()へ戻す(Review Focus 5)。
"""

from __future__ import annotations

import asyncio
import uuid
from datetime import timedelta

from helpers_nfr import (
    create_intent_nfr,
    create_pair_nfr,
    create_user_nfr,
    ensure_env_prerequisites,
    ensure_seed,
    intent_payload,
    wait_events_done,
    wait_until,
)

from conftest import (
    API_CONTAINERS,
    auth_header,
    docker_logs,
    get_clock,
    publish_event,
    record_item,
    workers_start,
    workers_stop,
)

NIL_UUID = "00000000-0000-0000-0000-000000000000"


def _api_log_matches(word: str) -> int:
    """api1/api2の両コンテナログ合算でのword出現件数(単一コマンド照合)。"""
    logs = "".join(docker_logs(c) for c in API_CONTAINERS)
    return logs.count(word)


async def test_n5_00_seed_and_env(api, db):
    """環境前提+シード投入(べき等・design §4.3手順1)。"""
    result = await ensure_seed(api, db)
    n_active = await db.fetchval("SELECT count(*) FROM intents WHERE status='active'")
    record_item(
        item_id="ws7-n5-00",
        name="環境前提とシード投入(べき等)",
        test="test_n5_00_seed_and_env",
        verdict="pass",
        observed={
            "skipped": result["skipped"],
            "users": result["users"],
            "active_intents": n_active,
        },
    )


async def test_n5_10_poison_payload_quarantined(db):
    """毒ペイロード(構造違反JSON)が5回再試行の末にquarantined隔離される
    (10 §4.7・06 §9)。失敗理由はpayload.failure_reasonへ保持。"""
    await ensure_env_prerequisites(db)
    before = await db.fetchval(
        "SELECT count(*) FROM match_events WHERE status='quarantined'"
    )
    publish_event("created", str(uuid.uuid4()), raw_payload='{"event_type": "created"}')
    await asyncio.sleep(45)  # 再試行5回(バックオフ1,2,4,8,16秒)+余裕
    row = await db.fetchrow(
        "SELECT status, payload->>'failure_reason' AS reason "
        "FROM match_events WHERE source_intent_id=$1 ORDER BY created_at DESC LIMIT 1",
        uuid.UUID(NIL_UUID),
    )
    assert row is not None, "毒ペイロードの行が挿入されていません"
    assert row["status"] == "quarantined", row
    assert row["reason"], "失敗理由がpayloadに保持されていません"
    after = await db.fetchval(
        "SELECT count(*) FROM match_events WHERE status='quarantined'"
    )
    record_item(
        item_id="ws7-n5-10",
        name="毒ペイロード→5回再試行→quarantined隔離+理由保持",
        test="test_n5_10_poison_payload_quarantined",
        verdict="pass",
        observed={
            "failure_reason": row["reason"],
            "quarantined_before": before,
            "quarantined_after": after,
        },
    )


async def test_n5_20_deleted_reference_processed_discard(api, db):
    """削除済みIntent参照Eventはquarantinedでなくprocessedで閉じる
    (06 §9・破棄理由payload.discard_reason=intent_not_found)。

    同一version再publishは既存processed行とのUNIQUE衝突でduplicate=ackに
    なり理由が記録されないため・削除後に存在しないversion(updated v2)を
    publishして新3点組を作る(§2.1読み替えに相当する観察設計)。
    """
    await ensure_env_prerequisites(db)
    subject = "ws7-n5-del"
    await create_user_nfr(api, subject)
    intent_id = await create_intent_nfr(
        api, subject, intent_payload(seq=9100, now=get_clock().now())
    )
    r = await api.delete(f"/v1/intents/{intent_id}", headers=auth_header(subject))
    assert r.status_code in (200, 204), r.text
    await asyncio.sleep(15)  # 削除Event処理(cascade物理削除)
    publish_event("updated", intent_id, version=2)
    await asyncio.sleep(30)  # INSERT→worker処理
    row = await db.fetchrow(
        "SELECT status, payload->>'discard_reason' AS reason "
        "FROM match_events WHERE source_intent_id=$1 AND event_type='updated' "
        "AND payload->>'version'='2'",
        uuid.UUID(intent_id),
    )
    assert row is not None, "updated v2の行がありません"
    assert row["status"] == "processed", row
    assert row["reason"] == "intent_not_found", row
    record_item(
        item_id="ws7-n5-20",
        name="削除済み参照Event→processed破棄(理由=intent_not_found)",
        test="test_n5_20_deleted_reference_processed_discard",
        verdict="pass",
        observed={"status": row["status"], "discard_reason": row["reason"]},
    )


async def test_n5_30_unique_duplicate_no_double_candidates(api, db):
    """同一Event 2回投入でUNIQUE制約により重複排除
    (match_events 1行のまま・候補二重生成なし)。"""
    pair = await create_pair_nfr(api, db, prefix="n5dup", seq=9200)
    n_events = await db.fetchval(
        "SELECT count(*) FROM match_events WHERE source_intent_id=$1 "
        "AND event_type='created' AND payload->>'version'='1'",
        uuid.UUID(pair["intent_a"]),
    )
    n_cand = await db.fetchval("SELECT count(*) FROM match_candidates")
    publish_event("created", pair["intent_a"], version=1)  # 同一3点組の再投下
    await asyncio.sleep(30)
    n_events2 = await db.fetchval(
        "SELECT count(*) FROM match_events WHERE source_intent_id=$1 "
        "AND event_type='created' AND payload->>'version'='1'",
        uuid.UUID(pair["intent_a"]),
    )
    n_cand2 = await db.fetchval("SELECT count(*) FROM match_candidates")
    assert n_events == 1, n_events
    assert n_events2 == 1, f"重複行が生成された: {n_events2}"
    assert n_cand2 == n_cand, f"候補が二重生成された: {n_cand}→{n_cand2}"
    record_item(
        item_id="ws7-n5-30",
        name="UNIQUE重複排除(同一Event再投下で行・候補とも不変)",
        test="test_n5_30_unique_duplicate_no_double_candidates",
        verdict="pass",
        observed={
            "match_events": n_events2,
            "match_candidates": f"{n_cand}→{n_cand2}",
        },
    )


async def test_n5_40_no_propagation_after_poison(api, db):
    """毒投入後も後続イベントは滞留せず提案まで到達する(非波及・10 §4.7)。"""
    before = await db.fetchval(
        "SELECT count(*) FROM latch_status_events WHERE to_status='proposed'"
    )
    await create_pair_nfr(api, db, prefix="n5prop", seq=9300)
    after = await db.fetchval(
        "SELECT count(*) FROM latch_status_events WHERE to_status='proposed'"
    )
    assert after > before, "毒後の正常ペアで提案が発生していません(波及の疑い)"
    record_item(
        item_id="ws7-n5-40",
        name="毒後の非波及(正常ペアが提案まで到達)",
        test="test_n5_40_no_propagation_after_poison",
        verdict="pass",
        observed={"proposed_before": before, "proposed_after": after},
    )


async def test_n5_50_lag_alert_and_resolve(api, db):
    """worker停止で滞留を作り・API側のlag alertが1回発報→解除resolve
    (10 §4.7・design §2.5案A・単発化はRedisアームキー)。

    滞留の作り方はAPI作成(created)である(§2.1読み替え10): match_events行
    (status=pending)はAPI側のoutbox(tx内INSERT・intents/events.py)で作られ・
    worker停止中も残る。CLI publishはPub/Sub送信のみでworker停止中は行を
    作らないため使わない。年齢はlag_alertのClock差分(=APIのControlledClock)
    で計算されるため・staging clock advanceで年齢条件を仮想時計で成立させ・
    tick(60秒・実時間)の発報はwait_untilで待つ。fallback relayが仮想時計
    前進でpending行を再publishし続けるが・worker再開後の3点組重複(UNIQUE・
    duplicate=ack)で冪等=無害。
    """
    await ensure_env_prerequisites(db)
    clock = get_clock()
    await create_user_nfr(api, "ws7-n5lag")  # users作成はworker停止前に
    workers_stop()
    try:
        # pending行を作る(API outbox=tx内INSERT・worker停止中は消化されない)
        await create_intent_nfr(
            api, "ws7-n5lag", intent_payload(seq=9700, now=clock.now())
        )
        n_pending = await db.fetchval(
            "SELECT count(*) FROM match_events WHERE status='pending'"
        )
        assert n_pending >= 1, f"pending行が生えていない: {n_pending}"
        # 年齢条件(>=300秒)を仮想Clockで成立させる(tickは実時間・発報を待つ)
        clock.advance(timedelta(seconds=310))
        await wait_until(
            lambda: _api_log_matches("queue lag alert") == 1,
            timeout_s=120,
            interval_s=10,
            what="queue lag alertの発報(api両コンテナ合算で1回)",
        )
    finally:
        workers_start()
    await wait_events_done(db, timeout_s=300)  # 消化(年齢条件の解除)
    await wait_until(
        lambda: _api_log_matches("queue lag resolve") >= 1,
        timeout_s=120,
        interval_s=10,
        what="queue lag resolveの記録",
    )
    n_alert = _api_log_matches("queue lag alert")
    n_resolve = _api_log_matches("queue lag resolve")
    assert n_alert == 1, f"lag alertの発報は1回: {n_alert}"
    record_item(
        item_id="ws7-n5-50",
        name="Queue滞留→lag alert 1回発報→解除resolve(worker停止含む)",
        test="test_n5_50_lag_alert_and_resolve",
        verdict="pass",
        observed={
            "alert_count": n_alert,
            "resolve_count": n_resolve,
            "pending_rows_at_stop": n_pending,
        },
    )
