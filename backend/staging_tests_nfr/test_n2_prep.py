"""n2 prep: 復旧試験の母体作成(10 §4.4・§2.1読み替え12)。

実行順序はn5→n4→n3→n2(design §4.2・順序変更なし)で・n3の月跨ぎClock
ジャンプにより既存Intentはexpired化済み。そのままではactive数0でdumpし
cross-checkが0==0で自明化するため・dump直前にfreshペア2組を作りactive
母体を確保する(手順§8の5-0で実行)。
"""

from __future__ import annotations

from helpers_nfr import create_pair_nfr, ensure_env_prerequisites

from conftest import record_item


async def test_n2_00_prep_active_population(api, db):
    """dump直前のactive母体を確保する(freshペア2組)。"""
    await ensure_env_prerequisites(db)
    await create_pair_nfr(api, db, prefix="n2p1", seq=9650)
    await create_pair_nfr(api, db, prefix="n2p2", seq=9652)
    n_active = await db.fetchval("SELECT count(*) FROM intents WHERE status='active'")
    assert n_active >= 4, f"active母体が足りません: {n_active}"
    record_item(
        item_id="ws7-n2-00",
        name="dump直前のactive母体確保(freshペア2組)",
        test="test_n2_00_prep_active_population",
        verdict="pass",
        observed={"active_intents": n_active},
    )
