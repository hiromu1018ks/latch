"""n2: バックアップ復旧(10 §4.4)。

前提(§8手順5): 5-0 prep(freshペア2組・test_n2_prep.py)→worker停止→
make staging-dump→staging-db2-up→staging-restore→staging-db2-switch済み。
既定DB(15432)はworker停止中に静止したため・そのactive数=ダンプ時点の値
=cross-checkの「前」。db2(16543)が「後」。
復旧後の再エンベディングは識別子記録(embedding_model)で対象を特定して
NULL化し・backfillで回収する(01 §18・design §2.2案A)。
"""

from __future__ import annotations

from helpers_nfr import (
    create_intent_nfr,
    create_user_nfr,
    get_clock,
    intent_payload,
    wait_pipeline_settled,
    wait_until,
)

from conftest import (
    auth_header,
    record_item,
)


async def test_n2_10_preconditions(db, db_recovery_rw):
    """実施前提の名前つき失敗: 両DB到達・既定DBの静止(dump時点の保証)。"""
    ver = await db_recovery_rw.fetchval("SELECT version_num FROM alembic_version")
    assert ver == "0008", f"db2のheadが0008ではありません: {ver}(staging-restoreを実行)"
    n_pending_default = await db.fetchval(
        "SELECT count(*) FROM match_events WHERE status='pending'"
    )
    assert n_pending_default == 0, (
        "既定DBにpendingが残っています(worker停止→dump→switchの手順を再確認)"
    )
    n_active_default = await db.fetchval(
        "SELECT count(*) FROM intents WHERE status='active'"
    )
    assert n_active_default > 0, (
        "既定DBのactive Intentが0件です(手順1のシード投入を確認)"
    )


async def test_n2_20_cross_check_active_count(db, db_recovery_rw):
    """cross-check: 復旧前後のactive Intent数一致(10 §4.4)。"""
    before = await db.fetchval("SELECT count(*) FROM intents WHERE status='active'")
    after = await db_recovery_rw.fetchval(
        "SELECT count(*) FROM intents WHERE status='active'"
    )
    assert before == after, f"active数不一致: 既定DB={before} db2={after}"
    record_item(
        item_id="ws7-n2-20",
        name="cross-check: 復旧前後のactive Intent数一致",
        test="test_n2_20_cross_check_active_count",
        verdict="pass",
        observed={"before(15432)": before, "after(16543)": after},
    )


async def test_n2_30_reembedding_via_identifier_null_and_backfill(db_recovery_rw):
    """識別子記録(embedding_model)で再エンベディング対象を特定→NULL化→
    backfill回収(01 §18・design §2.2案A)。

    NULL化・回収の対象はactive Intentに限定する(supervisor検証fix 2026-10-05):
    BackfillRunnerの対象抽出はstatus='active'かつembedding IS NULLのため・
    期限切れ(expired)行をNULL化しても回収されず「全件回収」の待ちが成立
    しない(backfill.py _SELECT_BACKFILL_TARGETSの実装どおり・期限切れIntent
    の再エンベディングは復旧要件の対象外)。"""
    model_before = await db_recovery_rw.fetchval(
        "SELECT DISTINCT embedding_model FROM intents "
        "WHERE status='active' AND embedding IS NOT NULL "
        "AND embedding_model IS NOT NULL LIMIT 1"
    )
    assert model_before, "embedding済み行がありません(staging-restoreを確認)"
    targets = await db_recovery_rw.fetchval(
        "SELECT count(*) FROM intents "
        "WHERE status='active' AND embedding IS NOT NULL "
        "AND embedding_model IS NOT NULL"
    )
    result = await db_recovery_rw.execute(
        "UPDATE intents SET embedding=NULL, embedding_model=NULL "
        "WHERE status='active' AND embedding IS NOT NULL "
        "AND embedding_model IS NOT NULL"
    )
    assert f"UPDATE {targets}" in result, (targets, result)
    assert targets > 0

    async def recovered() -> bool:
        n = await db_recovery_rw.fetchval(
            "SELECT count(*) FROM intents "
            "WHERE status='active' AND embedding IS NOT NULL "
            "AND embedding_model IS NOT NULL"
        )
        return int(n) == targets

    await wait_until(
        recovered,
        timeout_s=600,
        interval_s=10,
        what=(
            f"backfillによるembedding回収(active {targets}件・"
            "recovery overrideでinterval 5秒)"
        ),
    )
    record_item(
        item_id="ws7-n2-30",
        name="識別子特定→embedding NULL化→backfillで再エンベディング",
        test="test_n2_30_reembedding_via_identifier_null_and_backfill",
        verdict="pass",
        observed={
            "embedding_model": model_before,
            "nulled_rows": targets,
            "recovered_rows": targets,
        },
    )


async def test_n2_40_smoke_proposal_after_recovery(api, db_recovery_rw):
    """復旧後のスモーク: Intent検索・マッチング・通知が正常
    (新規ペア→提案→通知記録・10 §4.4)。"""
    before_notif = await db_recovery_rw.fetchval(
        "SELECT count(*) FROM notifications WHERE type='proposal'"
    )
    now = get_clock().now()
    await create_user_nfr(api, "ws7-n2a")
    await create_user_nfr(api, "ws7-n2b")
    intent_a = await create_intent_nfr(
        api, "ws7-n2a", intent_payload(seq=9600, now=now)
    )
    intent_b = await create_intent_nfr(
        api, "ws7-n2b", intent_payload(seq=9601, now=now)
    )
    await wait_pipeline_settled(db_recovery_rw)
    r = await api.get("/v1/latches", headers=auth_header("ws7-n2a"))
    assert r.status_code == 200, r.text
    proposed = await db_recovery_rw.fetchval(
        "SELECT count(*) FROM latch_status_events WHERE to_status='proposed'"
    )
    assert proposed >= 1, "復旧後に提案が発生していません(マッチング不成立)"
    after_notif = await db_recovery_rw.fetchval(
        "SELECT count(*) FROM notifications WHERE type='proposal'"
    )
    assert after_notif > before_notif, "通知記録が発生していません"
    r = await api.get("/v1/notifications", headers=auth_header("ws7-n2a"))
    assert r.status_code == 200, r.text
    record_item(
        item_id="ws7-n2-40",
        name="復旧後スモーク(検索・マッチング・通知が正常)",
        test="test_n2_40_smoke_proposal_after_recovery",
        verdict="pass",
        observed={
            "latches_http": r.status_code,
            "proposed": proposed,
            "notifications": f"{before_notif}→{after_notif}",
            "intents": [intent_a, intent_b],
        },
    )
