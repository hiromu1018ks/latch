"""T7: 解析の再利用と手動修正保護(計画 §6 T7・§5.5・I4)。

検証面:
- 同文並行20 → 実LLM呼出1回(同一実行内集約)・全員同一結果
- 別ユーザー・別JST基準日・別テキスト・別版は別キー(miss)
- 23:59→00:00の日跨ぎで同文でも再解析(基準日が変わる)
- 手動修正(manual)は再解析で上書きされない・parser由来は置き換わる
- 保存済み編集の再利用は非永続集約の範囲(実行をまたぐ重複は防げない
  ことを記録)・永続cacheは保存契約承認まで導入しない
"""

from __future__ import annotations

import asyncio
from datetime import date

import pytest

from latch.domain.parse_key import (
    PROVENANCE_MANUAL,
    PROVENANCE_PARSER,
    FieldProvenance,
    ParseRequestAggregator,
    ParseVersions,
    build_parse_key,
    merge_reparse,
)

V = ParseVersions(
    prompt="parser-1", schema="g1-1", model="claude-haiku-4-5", normalizer="norm-1"
)
TEXT = "今度の金曜の夜、天文館あたりで軽くごはん。"


def _key(*, actor="user-a", text=TEXT, basis=date(2026, 10, 8), versions=V):
    return build_parse_key(
        actor_scope=actor,
        text=text,
        current_date=basis,
        timezone_name="Asia/Tokyo",
        versions=versions,
    )


async def _stub_send(results_log: list):
    async def send(key):
        await asyncio.sleep(0.01)  # 並行窓を作る
        results_log.append(key.actor_scope)
        return {"structured_intent": {"echo": key.text_fingerprint[:16]}}

    return send


# =============================================================================
# 並行集約(同一実行内)
# =============================================================================


async def test_same_text_20_parallel_single_call():
    """同文20並行: 実呼出1回・全員同一結果・B0(毎回20回)との差。"""
    agg = ParseRequestAggregator()
    log: list = []
    send = await _stub_send(log)
    key = _key()
    results = await asyncio.gather(*[agg.parse(key, send) for _ in range(20)])
    assert agg.calls == 1, f"B0は20回・集約後は1回(実測 {agg.calls})"
    assert len(log) == 1
    assert all(r == results[0] for r in results)


async def test_distinct_requests_not_aggregated():
    """別ユーザー・別基準日・別テキスト・別版は別キー(全て実行)。"""
    agg = ParseRequestAggregator()
    log: list = []
    send = await _stub_send(log)
    keys = [
        _key(actor="user-a"),
        _key(actor="user-b"),  # 別ユーザー(他人の結果を流用しない)
        _key(basis=date(2026, 10, 9)),  # JST日跨ぎ
        _key(text=TEXT + " 追記"),  # 厳密なtext違い
        _key(versions=ParseVersions("parser-2", V.schema, V.model, V.normalizer)),
    ]
    await asyncio.gather(*[agg.parse(k, send) for k in keys])
    assert agg.calls == 5


async def test_midnight_rollover_changes_key():
    """23:59→00:00: 同文でも基準日が変わるため別キー。"""
    k1 = _key(basis=date(2026, 10, 8))
    k2 = _key(basis=date(2026, 10, 9))
    assert k1.cache_id() != k2.cache_id()
    assert k1.text_fingerprint == k2.text_fingerprint, "textは同じでも"


async def test_failure_propagates_to_all_waiters():
    """集約中の失敗は全員へ伝播(結果を捏造しない)。"""
    agg = ParseRequestAggregator()

    async def failing(key):
        await asyncio.sleep(0.01)
        raise RuntimeError("LLM_UNAVAILABLE")

    key = _key()
    results = await asyncio.gather(
        *[agg.parse(key, failing) for _ in range(5)], return_exceptions=True
    )
    assert all(isinstance(r, RuntimeError) for r in results)
    assert agg.calls == 1


# =============================================================================
# 手動修正の保護(§5.5)
# =============================================================================


def test_manual_fields_survive_reparse():
    """manual修正は再解析で上書きされない・parser由来は置き換わる。"""
    prov = FieldProvenance()
    prov.mark("time_start", PROVENANCE_MANUAL)
    prov.mark("location_name", PROVENANCE_PARSER)
    prov.mark("budget_max", PROVENANCE_MANUAL)

    current = {
        "time_start": "2026-10-10T19:00:00+09:00",  # 手動修正済み
        "location_name": "天文館",
        "budget_max": 4000,  # 手動修正済み
        "participants_min": 2,
    }
    new_parsed = {
        "time_start": "2026-10-08T19:00:00+09:00",  # 再解析結果(古い)
        "location_name": "鹿児島中央駅",  # 再解析で変わる(parser由来)
        "budget_max": 3000,
        "participants_min": 3,
    }
    merged = merge_reparse(
        current_values=current, new_parsed=new_parsed, provenance=prov
    )
    assert merged["time_start"] == current["time_start"], "手動修正優先"
    assert merged["budget_max"] == current["budget_max"], "手動修正優先"
    assert merged["location_name"] == "鹿児島中央駅", "parser由来は置換"
    assert merged["participants_min"] == 3


def test_raw_text_same_but_manual_conditions_win():
    """raw_textが同じでも手動条件変更は条件側が勝つ(§5.5)。"""
    prov = FieldProvenance()
    prov.mark("participants_min", PROVENANCE_MANUAL)
    current = {"participants_min": 4}  # 手動で2→4へ変更済み
    new_parsed = {"participants_min": 2}  # 同textの再解析は2を返す
    merged = merge_reparse(
        current_values=current, new_parsed=new_parsed, provenance=prov
    )
    assert merged["participants_min"] == 4


def test_invalid_provenance_rejected():
    prov = FieldProvenance()
    with pytest.raises(ValueError):
        prov.mark("x", "unknown-source")


# =============================================================================
# 非永続集約の限界の記録(§5.5)
# =============================================================================


async def test_aggregation_scope_is_execution_local():
    """実行をまたぐ集約はしない(別Aggregator=別実行では重複する)。"""
    agg1 = ParseRequestAggregator()
    agg2 = ParseRequestAggregator()
    log: list = []
    send = await _stub_send(log)
    key = _key()
    await asyncio.gather(agg1.parse(key, send), agg2.parse(key, send))
    # 実行間では集約されない(2回) — 永続化は保存契約の承認待ち
    assert agg1.calls + agg2.calls == 2


def test_parse_key_contains_no_raw_text():
    """ParseKey自体は原文を保持しない(fingerprintのみ・機微非混入)。"""
    key = _key()
    assert TEXT not in key.cache_id()
    assert TEXT not in key.text_fingerprint
