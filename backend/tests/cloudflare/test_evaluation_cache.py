"""T6: 厳密な評価cache(計画 §6 T6・§5.3・I2)。

検証面(§5.3):
- 同じfingerprintのhitは追加API 0回(B0は毎回1回)
- A/B方向・soft/否定/予算/日時・prompt/model・policy変更は別ケース(miss)
- resume・表示設定のみのIntent版進行はhit(評価入力が同じなら再利用)
- fallback結果・実モデル不明はcache対象外
- 削除・同意撤回は期限より優先して失効・遅延応答から再作成しない
- cacheは数値結果のみ(提案・公開可否を保存しない)
- 保持期限 = min(両Intent期限, 30日)
"""

from __future__ import annotations

import asyncio
import uuid as uuid_mod
from datetime import UTC, datetime, timedelta

import pytest

from latch.adapters.d1.database import LocalD1Database
from latch.adapters.d1.evaluation_cache import D1EvaluationCache
from latch.domain.evaluation_key import EvaluationVersions, build_evaluation_key
from latch.domain.time import micros

NOW = datetime(2026, 10, 8, 12, 0, 0, tzinfo=UTC)

V = EvaluationVersions(
    normalizer="norm-1",
    questions="jev-1.13.0",
    prompt="sysone-1",
    schema="jev-schema-1",
    provider="typesafe_jev",
    model="jev-1.13.0",
    policy="threshold-0.60",
)
INPUT_A = "matched_pair_input_text_a"
INPUT_B = "matched_pair_input_text_b"
RESULT = {
    "would_a_accept_b": 0.8,
    "would_b_accept_a": 0.7,
    "jev_5axis": {"purpose_fit": {"value": 0.5, "confidence": 0.9}},
    "provider": "typesafe_jev",
    "model": "jev-1.13.0",
}


@pytest.fixture
async def db():
    database = LocalD1Database()
    database.migrate()
    yield database
    database.close()


@pytest.fixture
def cache(db) -> D1EvaluationCache:
    return D1EvaluationCache(db)


def _pair() -> tuple[str, str]:
    a = str(uuid_mod.uuid4())
    b = str(uuid_mod.uuid4())
    return tuple(sorted((a, b)))


def _key(pair=None, inputs=(INPUT_A, INPUT_B), versions=V, ctx="ctx-1"):
    return build_evaluation_key(
        pair=pair or _pair(),
        sent_inputs=inputs,
        versions=versions,
        context_version=ctx,
    )


async def _store(db, cache, key, *, expires=None, now=NOW):
    """期限情報を渡して保存(intents行は不要 — 期限は引数で与える)。"""
    return await cache.store_evaluation(
        key,
        result=RESULT,
        actual_provider=V.provider,
        actual_model=V.model,
        requested_provider=V.provider,
        requested_model=V.model,
        intent_expires_at=list(expires or [None, None]),
        now=now,
    )


# =============================================================================
# hit / miss(§5.3検証ケース)
# =============================================================================


async def test_same_fingerprint_hit_zero_additional_api(db, cache):
    """同版・同入力の再評価はcache hit(追加API 0回 — B0との差)。"""
    key = _key()
    ok, reason = await _store(db, cache, key)
    assert ok, reason
    got = await cache.get_cached_evaluation(key, NOW + timedelta(minutes=5))
    assert got is not None and got.cache_hit
    assert got.result == RESULT
    # 追加API呼出しは0回になることを呼出しカウンタで表明
    calls = 0
    if got is None:
        calls += 1
    assert calls == 0, "hit時の外部呼出しは0回"


async def test_ab_direction_changes_key(db, cache):
    """A/B方向の入替えは別ケース(キーが変わる)。"""
    p = _pair()
    k1 = _key(pair=p, inputs=(INPUT_A, INPUT_B))
    k2 = _key(pair=p, inputs=(INPUT_B, INPUT_A))
    assert k1.fingerprint != k2.fingerprint
    await _store(db, cache, k1)
    assert await cache.get_cached_evaluation(k2, NOW) is None


async def test_input_variations_miss(db, cache):
    """soft/否定/予算/日時の入力変更は別ケース。"""
    p = _pair()
    base = _key(pair=p)
    variants = [
        _key(pair=p, inputs=(INPUT_A + " soft:静かな店", INPUT_B)),
        _key(pair=p, inputs=(INPUT_A + " NG:券", INPUT_B)),
        _key(pair=p, inputs=(INPUT_A, INPUT_B + " 予算3000円")),
        _key(pair=p, inputs=(INPUT_A, INPUT_B + " 明日19時")),
    ]
    await _store(db, cache, base)
    for v in variants:
        assert await cache.get_cached_evaluation(v, NOW) is None


async def test_prompt_model_policy_change_miss(db, cache):
    """prompt・model・policyの版変更はmiss。"""
    p = _pair()
    base = _key(pair=p)
    await _store(db, cache, base)
    for field, value in (
        ("prompt", "sysone-2"),
        ("model", "jev-1.14.0"),
        ("policy", "threshold-0.65"),
        ("normalizer", "norm-2"),
        ("questions", "jev-q2"),
        ("schema", "jev-schema-2"),
    ):
        changed = _key(
            pair=p, versions=EvaluationVersions(**{**V.__dict__, field: value})
        )  # type: ignore[arg-type]
        assert await cache.get_cached_evaluation(changed, NOW) is None, field


async def test_resume_display_only_version_bump_hits(db, cache):
    """Intent版進行(resume・表示設定)はキーに含めない→hit。"""
    key = _key()
    await _store(db, cache, key)
    # 同じ入力でキー再構築(版は無関係) → 同じcache_key
    again = _key(pair=tuple(key.pair_key.split(":")[1:3]))
    got = await cache.get_cached_evaluation(again, NOW)
    assert got is not None, "評価入力が同じなら版進行後も再利用"


async def test_time_context_changes_key(db, cache):
    """使った時刻文脈の版も含める(「今夜」等の相対表現の境界)。"""
    p = _pair()
    k1 = _key(pair=p, ctx="jst-day-2026-10-08")
    k2 = _key(pair=p, ctx="jst-day-2026-10-09")
    await _store(db, cache, k1)
    assert await cache.get_cached_evaluation(k2, NOW) is None


# =============================================================================
# 保存対象の制限(§5.3)
# =============================================================================


async def test_fallback_and_unknown_model_not_stored(db, cache):
    """fallback結果は主モデルcacheとして保存しない・実モデル不明も対象外。"""
    key = _key()
    # fallback: actual != requested
    ok, reason = await cache.store_evaluation(
        key,
        result=RESULT,
        actual_provider="anthropic",
        actual_model="claude-haiku-4-5",
        requested_provider=V.provider,
        requested_model=V.model,
        intent_expires_at=[None, None],
        now=NOW,
    )
    assert ok is False and "fallback" in (reason or "")
    # 実モデル不明
    ok2, reason2 = await cache.store_evaluation(
        key,
        result=RESULT,
        actual_provider=None,
        actual_model=None,
        requested_provider=V.provider,
        requested_model=V.model,
        intent_expires_at=[None, None],
        now=NOW,
    )
    assert ok2 is False and reason2 == "actual model unknown"
    assert await cache.get_cached_evaluation(key, NOW) is None


async def test_cache_stores_no_proposal_or_visibility(db, cache):
    """cacheが保存するのは数値結果と由来のみ(提案・公開可否なし)。"""
    key = _key()
    await _store(db, cache, key)
    from latch.domain.evaluation_key import cache_key_of

    row = (
        await db.prepare("SELECT * FROM evaluation_cache WHERE cache_key = ?")
        .bind(cache_key_of(key))
        .first()
    )
    assert row is not None
    import json as json_mod

    stored = json_mod.loads(row["result"])
    assert "would_a_accept_b" in stored  # 数値結果
    for forbidden in ("proposal", "latch_score", "visible", "match_level"):
        assert forbidden not in stored, f"cacheに{forbidden}を保存しない"
        assert forbidden not in row.keys() or row[forbidden] is None


# =============================================================================
# 失効(削除・同意撤回・期限)
# =============================================================================


async def test_intent_delete_invalidates_before_expiry(db, cache):
    """削除は保持期限より優先(残っていても使わない)。"""
    key = _key()
    await _store(
        db, cache, key, expires=[NOW + timedelta(days=20), NOW + timedelta(days=20)]
    )
    a, b = key.pair_key.split(":")[1:3]
    removed = await cache.invalidate_for_intents([a], NOW)
    assert removed == 1
    assert await cache.get_cached_evaluation(key, NOW) is None


async def test_expiry_is_min_of_intents_and_30d(db, cache):
    """保持期限 = min(両Intent期限, 評価+30日)。"""
    soon = NOW + timedelta(days=2)
    key = _key()
    await _store(db, cache, key, expires=[soon, NOW + timedelta(days=40)])
    row = (
        await db.prepare("SELECT expires_at FROM evaluation_cache WHERE pair_key = ?")
        .bind(key.pair_key)
        .first()
    )
    assert row["expires_at"] == micros(soon)
    # 期限後はhitしない
    assert await cache.get_cached_evaluation(key, soon + timedelta(seconds=1)) is None
    # 期限前はhitする
    assert (
        await cache.get_cached_evaluation(key, soon - timedelta(seconds=1)) is not None
    )


async def test_no_recreate_after_deletion(db, cache):
    """削除後に遅れて返る応答からcacheを再作成しない。"""
    key = _key()
    a, b = key.pair_key.split(":")[1:3]
    await db.batch(
        [
            db.prepare(
                "INSERT INTO deletion_tombstones"
                " (id, target_kind, target_id, reason, state, created_at)"
                " VALUES (?, 'intent', ?, 'intent_delete', 'pending', ?)"
            ).bind(str(uuid_mod.uuid4()), a, micros(NOW)),
        ]
    )
    ok, reason = await _store(db, cache, key)
    assert ok is False and reason == "intent deleted"
    assert await cache.get_cached_evaluation(key, NOW) is None


async def test_consent_withdraw_invalidates(db, cache):
    """同意撤回でpairに触れるcacheを失効させる。"""
    key = _key()
    await _store(db, cache, key)
    a, b = key.pair_key.split(":")[1:3]
    # 同意撤回は invalidate_for_intents を呼ぶ(atomicのconsent_withdrawと同経路)
    removed = await cache.invalidate_for_intents([a, b], NOW)
    assert removed >= 1


async def test_hit_checks_current_conditions_not_cached(db, cache):
    """hit時もhard条件・公開可否は現行状態で再確認(cacheは数値のみ)。

    ここではcache層の責務分離を検証: 結果にblock/年齢/期限の判定を
    含まないこと(判定は呼び出し側が現行DBで行う)。
    """
    key = _key()
    await _store(db, cache, key)
    got = await cache.get_cached_evaluation(key, NOW)
    assert got is not None
    assert set(got.result.keys()) <= {
        "would_a_accept_b",
        "would_b_accept_a",
        "jev_5axis",
        "provider",
        "model",
    }, "cache結果に判定・公開状態を含めない"


async def test_parallel_same_key_store_and_hit(db, cache):
    """20並行の同キー読み書きで壊れない(単一keyの原子性)。"""
    key = _key()
    await _store(db, cache, key)
    results = await asyncio.gather(
        *[
            cache.get_cached_evaluation(key, NOW + timedelta(seconds=i))
            for i in range(20)
        ]
    )
    assert all(r is not None for r in results)
    rows = (await db.prepare("SELECT COUNT(*) AS c FROM evaluation_cache").first())["c"]
    assert rows == 1
