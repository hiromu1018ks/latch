"""n4: resume再評価とcatch-up回収(10 §4.5・FR-18)。

(a) resume: version+1のupdated EventでEmbeddingの再実行なくCandidate
Retrievalから再評価(embedding3列不変+embedding_completed再発行)。
(b) catch-up: worker停止+Clock前進でBucket到来Eventの欠落を作り、worker再開
後のcatch-upスキャン(60秒周期・Event発行せず直接投入)が回収する。観察は
reevalガードキー(§2.1読み替え9)+embedding列の状態照合。
worker停止は必ずtry/finallyで戻す(Review Focus 5)。
"""

from __future__ import annotations

import asyncio
import uuid
from datetime import timedelta

import redis.asyncio as aioredis
from helpers_nfr import (
    create_intent_nfr,
    create_pair_nfr,
    create_user_nfr,
    current_version,
    ensure_env_prerequisites,
    intent_payload,
    wait_events_done,
    wait_pipeline_settled,
    wait_until,
)

from conftest import (
    REDIS_URL,
    auth_header,
    get_clock,
    record_item,
    workers_start,
    workers_stop,
)


async def test_n4_10_resume_reeval_without_embedding(api, db):
    """(a) pause→Bucket経過→resume: Embedding再実行なく再評価へ復帰
    (10 §4.5(a)・相手Intentは別ユーザー配置=supervisor訂正2)。"""
    await ensure_env_prerequisites(db)
    pair = await create_pair_nfr(api, db, prefix="n4res", seq=9400)
    intent_a = pair["intent_a"]
    subject_a = pair["subject_a"]
    # resume前の状態記録(embedding3列+completed行数+相手候補)
    row_before = await db.fetchrow(
        "SELECT embedding::text AS emb, embedding_model, updated_at "
        "FROM intents WHERE id=$1",
        uuid.UUID(intent_a),
    )
    completed_before = await db.fetchval(
        "SELECT count(*) FROM match_events WHERE event_type='embedding_completed' "
        "AND source_intent_id=$1",
        uuid.UUID(intent_a),
    )
    # pause(仮想時刻基準のtransition)
    r = await api.post(f"/v1/intents/{intent_a}/pause", headers=auth_header(subject_a))
    assert r.status_code == 200, r.text
    paused = await db.fetchval(
        "SELECT status FROM intents WHERE id=$1", uuid.UUID(intent_a)
    )
    assert paused == "paused"
    # 対象開始時刻のBucketをClockで経過させる(10 §4.5(a)字義)。前進幅は
    # DB照合で計算する: (time_start−now)+600秒でstartを確実に通過する
    # (n4_20と同方式・仮想時刻依存を排除)。reevalガードの30分窓脱出
    # (1800秒)もこの前進で自然に満たされる(time_start=作成+3時間のため)
    time_start = await db.fetchval(
        "SELECT time_start FROM intents WHERE id=$1", uuid.UUID(intent_a)
    )
    assert time_start is not None
    advance_s = (time_start - get_clock().now()).total_seconds() + 600
    get_clock().advance(timedelta(seconds=advance_s))
    # resume(version+1のupdated Event・embedding不変の経路)
    r = await api.post(f"/v1/intents/{intent_a}/resume", headers=auth_header(subject_a))
    assert r.status_code == 200, r.text
    v_after_resume = await current_version(db, intent_a)
    assert v_after_resume == 2, f"resume後のversionは+1: {v_after_resume}"
    row_after_resume = await db.fetchrow(
        "SELECT embedding::text AS emb, embedding_model, updated_at "
        "FROM intents WHERE id=$1",
        uuid.UUID(intent_a),
    )
    await wait_pipeline_settled(db)
    row_after = await db.fetchrow(
        "SELECT embedding::text AS emb, embedding_model, updated_at "
        "FROM intents WHERE id=$1",
        uuid.UUID(intent_a),
    )
    completed_after = await db.fetchval(
        "SELECT count(*) FROM match_events WHERE event_type='embedding_completed' "
        "AND source_intent_id=$1",
        uuid.UUID(intent_a),
    )
    # Embeddingの再実行なし(embedding3列不変・updated_atも動かない)
    assert row_after["emb"] == row_after_resume["emb"], "embeddingが変化した"
    assert row_after["emb"] == row_before["emb"]
    assert row_after["embedding_model"] == row_before["embedding_model"]
    assert row_after["updated_at"] == row_after_resume["updated_at"]
    # 再評価(embedding_completedの再発行=resume由来の正規経路)
    assert completed_after > completed_before, "embedding_completedが再発行されていない"
    # 提案が発生し得る状態への復帰(候補の再評価完遂)
    cand = await db.fetchrow(
        "SELECT status, jev_result FROM match_candidates "
        "WHERE intent_a_id=$1 AND intent_b_id=$2 "
        "AND cheap_judge_score IS NOT NULL",
        uuid.UUID(intent_a),
        uuid.UUID(pair["intent_b"]),
    )
    assert cand is not None, "resume後の候補が見つかりません"
    assert cand["status"] == "evaluated", cand
    assert cand["jev_result"] is not None, "Jevまで走っていない"
    record_item(
        item_id="ws7-n4-10",
        name="resume→version+1→Embedding再実行なしで再評価・復帰",
        test="test_n4_10_resume_reeval_without_embedding",
        verdict="pass",
        observed={
            "version_after_resume": v_after_resume,
            "embedding_unchanged": True,
            "embedding_model": row_after["embedding_model"],
            "completed_before": completed_before,
            "completed_after": completed_after,
            "candidate_status": cand["status"],
        },
    )


async def test_n4_20_catchup_recovers_and_excludes_null_embedding(api, db):
    """(b) worker停止中のClock前進で欠落を作り・catch-upが回収する。
    embedding済み対象は処理され(reevalキー立)・embedding NULL対象は除外され
    backfillで回収される(06 §9・design §2.4(b))。"""
    await ensure_env_prerequisites(db)
    redis = aioredis.Redis.from_url(REDIS_URL, decode_responses=True)
    clock = get_clock()
    # 対象(i_n4c・expires_atを遠くへ=time_start=now+6日・FR-13で≈+72h候補)
    # 相手(i_n4e)・NULL化対象(i_n4d)の3件を別ユーザーで作る
    now = clock.now()
    await create_user_nfr(api, "ws7-n4c")
    await create_user_nfr(api, "ws7-n4e")
    await create_user_nfr(api, "ws7-n4d")
    intent_c = await create_intent_nfr(
        api, "ws7-n4c", intent_payload(seq=9500, now=now, start_hours=6 * 24)
    )
    # 相手Intentのidは照合に使わないため変数に入れない(F841回避)
    await create_intent_nfr(
        api, "ws7-n4e", intent_payload(seq=9501, now=now, start_hours=6 * 24)
    )
    intent_d = await create_intent_nfr(
        api, "ws7-n4d", intent_payload(seq=9502, now=now, start_hours=6 * 24)
    )
    await wait_pipeline_settled(db)
    expires_c = await db.fetchval(
        "SELECT expires_at FROM intents WHERE id=$1", uuid.UUID(intent_c)
    )
    expires_d = await db.fetchval(
        "SELECT expires_at FROM intents WHERE id=$1", uuid.UUID(intent_d)
    )
    assert expires_c is not None and expires_d is not None
    remaining = (expires_c - clock.now()).total_seconds()
    assert remaining > 7200, f"expires_at残が短い: {remaining}s"
    advance_s = remaining - 6000  # 「expires_atまで2時間を切る」状態を機械的に作る
    # ガードキー前掃除(観察の一意性)
    await redis.delete(f"reeval:{intent_c}")
    await redis.delete(f"reeval:{intent_d}")
    workers_stop()
    try:
        # NULL化(対象抽出条件の逆経路=embedding NULLはcatch-up除外・06 §9)
        import asyncpg

        conn = await asyncpg.connect("postgresql://latch:latch@127.0.0.1:15432/latch")
        n_null = await conn.execute(
            "UPDATE intents SET embedding=NULL, embedding_model=NULL WHERE id=$1",
            uuid.UUID(intent_d),
        )
        await conn.close()
        assert "UPDATE 1" in n_null, n_null
        # Bucket到来Eventのスケジューラが止まっている間にClockを前進=欠落
        clock.advance(timedelta(seconds=advance_s))
        workers_start()
        await asyncio.sleep(90)  # catch-upスキャン(60秒周期)+マージン
        key_c = await redis.get(f"reeval:{intent_c}")
        key_d = await redis.get(f"reeval:{intent_d}")
        assert key_c == "1", "catch-upがembedding済み対象を処理していない"
        assert key_d is None, "embedding NULLがcatch-upで処理された(除外が効いていない)"
        emb_d = await db.fetchval(
            "SELECT embedding IS NULL FROM intents WHERE id=$1", uuid.UUID(intent_d)
        )
        assert emb_d is True, "除外対象のembeddingが変化している"
        # backfillへの戻し(NULL化Intentがbackfill周期で回収される=観察完了後)
        await wait_events_done(db, timeout_s=30)

        async def recovered_now() -> bool:
            v = await db.fetchval(
                "SELECT embedding IS NOT NULL AND embedding_model IS NOT NULL "
                "FROM intents WHERE id=$1",
                uuid.UUID(intent_d),
            )
            return bool(v)

        await asyncio.sleep(300)  # backfill初回周期(300秒)
        await wait_until(
            recovered_now,
            timeout_s=600,
            what="backfillによるNULL化Intentの回収",
        )
        expires_d_after = await db.fetchval(
            "SELECT expires_at FROM intents WHERE id=$1", uuid.UUID(intent_d)
        )
        assert expires_d_after is not None
    finally:
        workers_start()
        await redis.aclose()
    record_item(
        item_id="ws7-n4-20",
        name="catch-up回収(embedding済み対象は処理・NULLは除外→backfill回収)",
        test="test_n4_20_catchup_recovers_and_excludes_null_embedding",
        verdict="pass",
        observed={
            "advance_s": advance_s,
            "reeval_key_c": key_c,
            "reeval_key_d_before_backfill": None,
            "backfill_recovered": True,
        },
    )
