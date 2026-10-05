"""n3: D-16縮退(10 §4.5残部・04 §5 D-16)。

alert境界(INCR戻り値==しきい値で1回)・上限到達(INCR>上限でdeny・skip・
提案ゼロ)・0時JSTリセット復帰・月次80%と日次の独立性・月初リセット。
実EventはEmbedding済みペアの一方へのpublish updated(§2.1読み替え5)・
各updatedの前にclock advance 1900秒(reevalガード30分窓の脱出)。
母体は試験ごとのfreshペア(§2.1読み替え11): 月跨ぎClockジャンプで既存
Intentはsweeperにexpired化されるため・キャッシュペアではupdatedが候補を
作らずJevが走らない。リセット後の試験(3_30/3_50)はfreshペア作成分の
INCRをdelete_counterで掃除してからpublishし・「INCR==1」を厳密に検証する。
旧キー残留のassertはそのまま(Redis TTLは実時間駆動のため仮想ジャンプで
消えない)。
"""

from __future__ import annotations

import uuid
from datetime import datetime, timedelta

from helpers_nfr import (
    create_pair_nfr,
    current_version,
    ensure_env_prerequisites,
    wait_pipeline_settled,
)

from conftest import (
    WORKER_CONTAINERS,
    docker_logs,
    get_clock,
    publish_event,
    record_item,
)
from latch.core.clock import JST
from latch.nfrtools.d16 import (
    daily_key,
    delete_counter,
    get_counter,
    monthly_key,
    set_counter,
)


def _next_day_jst_0005():
    now = get_clock().now().astimezone(JST)
    d = (now + timedelta(days=1)).date()
    return datetime(d.year, d.month, d.day, 0, 5, tzinfo=JST)


def _next_month_start_jst_0005():
    now = get_clock().now().astimezone(JST)
    year = now.year + (1 if now.month == 12 else 0)
    month = 1 if now.month == 12 else now.month + 1
    return datetime(year, month, 1, 0, 5, tzinfo=JST)


async def _publish_updated(api, db, pair) -> int:
    """ペアの一方へ次のversionのupdatedを投下し・収束を待つ(戻り値=投下version)。

    直近評価から30分以上経過させるため・投下前にclock advance 1900秒
    (reevalガード SET NX EX 1800の脱出)。
    """
    get_clock().advance(timedelta(seconds=1900))
    v = await current_version(db, pair["intent_a"]) + 1
    publish_event("updated", pair["intent_a"], version=v)
    await wait_pipeline_settled(db)
    return v


def _worker_log_matches(word: str) -> int:
    """worker1/worker2の両コンテナログ合算でのword出現件数。"""
    logs = "".join(docker_logs(c) for c in WORKER_CONTAINERS)
    return logs.count(word)


async def test_n3_10_daily_alert_at_24000(api, db, redis_nfr):
    """23,999→実Event→INCR=24,000でdaily_80pct alert(源流レポート添付)が
    丁度1回発報する(04 §5・guard実装の実経路確認)。"""
    await ensure_env_prerequisites(db)
    clock = get_clock()
    pair = await create_pair_nfr(api, db, prefix="n3d1", seq=9010)
    before_daily = await get_counter(redis_nfr, clock, "daily")
    before_alert = _worker_log_matches("daily_80pct")
    await set_counter(redis_nfr, clock, "daily", 23999)
    assert await get_counter(redis_nfr, clock, "daily") == 23999  # 読み戻し実査
    version = await _publish_updated(api, db, pair)
    assert await get_counter(redis_nfr, clock, "daily") == 24000
    assert _worker_log_matches("daily_80pct") - before_alert == 1, (
        "daily_80pct alertは1回"
    )
    # alert行のJSONをパースして源流レポート添付を確認(引用#7)
    import json as _json

    all_logs = "".join(docker_logs(c) for c in WORKER_CONTAINERS)
    alert_line = next(line for line in all_logs.splitlines() if "daily_80pct" in line)
    payload = _json.loads(alert_line[alert_line.index("{") :])
    assert "report" in payload, "alertに源流レポートが添付されていない"
    assert payload["report"]["intent_top"] is not None
    record_item(
        item_id="ws7-n3-10",
        name="D-16日次80%(24,000)でalert 1回発報+源流レポート添付",
        test="test_n3_10_daily_alert_at_24000",
        verdict="pass",
        observed={
            "daily_before": before_daily,
            "set_value": 23999,
            "daily_after": 24000,
            "alert_count": 1,
            "updated_version": version,
        },
    )


async def test_n3_20_daily_limit_deny_skip(api, db, redis_nfr):
    """30,000→実Event→INCR=30,001>30,000でdeny(global_daily)・候補skip・
    提案ゼロ(§2.1読み替え1・jev.py _skip_rowの実経路確認)。"""
    clock = get_clock()
    pair = await create_pair_nfr(api, db, prefix="n3d2", seq=9020)
    await set_counter(redis_nfr, clock, "daily", 30000)
    assert await get_counter(redis_nfr, clock, "daily") == 30000  # 読み戻し実査
    proposed_before = await db.fetchval(
        "SELECT count(*) FROM latch_status_events WHERE to_status='proposed'"
    )
    version = await _publish_updated(api, db, pair)
    daily_after = await get_counter(redis_nfr, clock, "daily")
    assert daily_after == 30001, daily_after
    skipped = await db.fetchrow(
        "SELECT status, skip_reason, jev_result FROM match_candidates "
        "WHERE (intent_a_id=$1 OR intent_b_id=$1) AND skip_reason='global_daily' "
        "ORDER BY id DESC LIMIT 1",
        uuid.UUID(pair["intent_a"]),
    )
    assert skipped is not None, "skip(global_daily)の候補行がありません"
    assert skipped["status"] == "skipped"
    assert skipped["jev_result"] is None, "Jev未判定保留がjev_result付きになっていない"
    proposed_after = await db.fetchval(
        "SELECT count(*) FROM latch_status_events WHERE to_status='proposed'"
    )
    assert proposed_after == proposed_before, "deny状態で提案が発生している"
    record_item(
        item_id="ws7-n3-20",
        name="D-16日次上限到達→Jevスキップ・『Jev未判定』保留・提案ゼロ",
        test="test_n3_20_daily_limit_deny_skip",
        verdict="pass",
        observed={
            "set_value": 30000,
            "daily_after": daily_after,
            "skip_reason": skipped["skip_reason"],
            "proposed_unchanged": proposed_before,
            "updated_version": version,
        },
    )


async def test_n3_30_daily_reset_recovers_at_jst_midnight(api, db, redis_nfr):
    """0時(JST)リセットで新日キーへ切替・実行が復帰する(04 §5)。

    月跨ぎ前の既存Intentはexpired化済みのため母体はfreshペア(読み替え11)。
    freshペア作成分のINCRをdelete_counterで掃除し・publishでINCR==1を厳密検証。
    """
    clock = get_clock()
    old_key = daily_key(clock)
    old_value = await redis_nfr.get(old_key)
    clock.set(_next_day_jst_0005())
    new_key = daily_key(clock)
    assert new_key != old_key
    assert await redis_nfr.get(new_key) is None  # 新キーは0から
    pair = await create_pair_nfr(api, db, prefix="n3d3", seq=9030)
    await delete_counter(redis_nfr, clock, "daily")  # 作成分のINCRを掃除
    version = await _publish_updated(api, db, pair)
    assert await get_counter(redis_nfr, clock, "daily") == 1  # 実行復帰(INCR=1)
    cand = await db.fetchrow(
        "SELECT status FROM match_candidates "
        "WHERE (intent_a_id=$1 OR intent_b_id=$1) AND status='evaluated' "
        "AND jev_result IS NOT NULL "
        "ORDER BY id DESC LIMIT 1",
        uuid.UUID(pair["intent_a"]),
    )
    assert cand is not None, "日次リセット後の実行復帰が確認できません"
    record_item(
        item_id="ws7-n3-30",
        name="D-16日次リセット(0時JST)で新日キー切替・実行復帰",
        test="test_n3_30_daily_reset_recovers_at_jst_midnight",
        verdict="pass",
        observed={
            "old_key": old_key,
            "old_value": old_value,
            "new_key": new_key,
            "new_value": 1,
            "updated_version": version,
        },
    )


async def test_n3_40_monthly_alert_independence(api, db, redis_nfr):
    """479,999→実Event→INCR=480,000でmonthly_80pctのみ発報
    (日次は未到達=両者の独立性の証明・10 §4.5字義)。"""
    clock = get_clock()
    pair = await create_pair_nfr(api, db, prefix="n3d4", seq=9040)
    daily_before = await get_counter(redis_nfr, clock, "daily")
    m_before = _worker_log_matches("monthly_80pct")
    d_before = _worker_log_matches("daily_80pct")
    await set_counter(redis_nfr, clock, "monthly", 479999)
    assert await get_counter(redis_nfr, clock, "monthly") == 479999
    version = await _publish_updated(api, db, pair)
    assert await get_counter(redis_nfr, clock, "monthly") == 480000
    assert _worker_log_matches("monthly_80pct") - m_before == 1, (
        "monthly alertが1回出ていない"
    )
    assert _worker_log_matches("daily_80pct") == d_before, "daily alertが同時に発報した"
    assert await get_counter(redis_nfr, clock, "daily") == daily_before + 1
    record_item(
        item_id="ws7-n3-40",
        name="D-16月次80%(480,000)alert発報・日次との独立性",
        test="test_n3_40_monthly_alert_independence",
        verdict="pass",
        observed={
            "monthly_set": 479999,
            "monthly_after": 480000,
            "monthly_alerts": 1,
            "daily_alerts_delta": 0,
            "daily_before": daily_before,
            "updated_version": version,
        },
    )


async def test_n3_50_monthly_reset_independence(api, db, redis_nfr):
    """暦月初JST 0時リセットで月次キーが新yyyymmへ切替・月次実行が復帰し・
    旧日次・旧月次キーはTTL内で残留する(04 §5・Clock操作で月初境界を進める)。

    月跨ぎ前の既存Intentはexpired化済みのため母体はfreshペア(読み替え11)。
    freshペア作成分のINCRをdelete_counterで掃除し・publishでINCR==1を厳密検証。
    """
    clock = get_clock()
    old_month_key = monthly_key(clock)
    old_day_key = daily_key(clock)
    old_month_value = await redis_nfr.get(old_month_key)
    old_day_value = await redis_nfr.get(old_day_key)
    clock.set(_next_month_start_jst_0005())
    new_month_key = monthly_key(clock)
    assert new_month_key != old_month_key
    assert await redis_nfr.get(new_month_key) is None
    assert await redis_nfr.get(old_month_key) == old_month_value, "旧月次キーが消えた"
    assert await redis_nfr.get(old_day_key) == old_day_value, "旧日次キーが消えた"
    pair = await create_pair_nfr(api, db, prefix="n3d5", seq=9050)
    await delete_counter(redis_nfr, clock, "monthly")  # 作成分のINCRを掃除
    version = await _publish_updated(api, db, pair)
    assert await get_counter(redis_nfr, clock, "monthly") == 1  # 月次実行復帰
    cand = await db.fetchrow(
        "SELECT status FROM match_candidates "
        "WHERE (intent_a_id=$1 OR intent_b_id=$1) AND status='evaluated' "
        "AND jev_result IS NOT NULL "
        "ORDER BY id DESC LIMIT 1",
        uuid.UUID(pair["intent_a"]),
    )
    assert cand is not None, "月次リセット後の実行復帰が確認できません"
    record_item(
        item_id="ws7-n3-50",
        name="D-16月次リセット(暦月初JST)・旧キー残留と実行復帰の独立性",
        test="test_n3_50_monthly_reset_independence",
        verdict="pass",
        observed={
            "old_month_key": old_month_key,
            "old_month_value": old_month_value,
            "new_month_key": new_month_key,
            "old_day_key_value_kept": old_day_value is not None,
            "new_month_value": 1,
            "updated_version": version,
        },
    )
