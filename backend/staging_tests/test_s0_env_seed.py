"""S0: 環境前提・シード投入・収束待ち・L1無向突合+D-08事前集計(設計§2.3)。

ここが落ちたら後続(S1〜S5)は全て連鎖失敗する — 設計§4-2どおり名前つき
失敗で原因を明示する。
"""

from __future__ import annotations

from helpers_seed import ex_pair_ids, fetch_candidates_pairs, load_seed, seed_all

from conftest import EXPECTED_HEAD, record_item


async def test_s0_environment_prerequisites(api, db):
    await seed_all(api, db)  # ensure_env_prerequisites込み(head=0007・geo・鍵)
    r = await api.get("/health")
    assert r.status_code == 200, "staging API(18000)が疎通していません(make staging-up)"
    record_item(
        item_id="S0-env",
        name="環境前提(8サービス・head・geo・鍵)",
        test="test_s0_environment_prerequisites",
        verdict="pass",
        observed={"alembic_head": EXPECTED_HEAD, "health": r.json()["status"]},
    )


async def test_s0_seed_ingestion(api, db):
    state = await seed_all(api, db)
    assert state["users_ok"] == 100, "users投入が100人でない"
    assert state["consent_ok"] == 100, "consent付与が100人でない"
    assert state["intents_ok"] == 300, "intents投入が300件でない"
    record_item(
        item_id="S0-seed",
        name="シード投入(100人×consent+300 Intent・422/429=0)",
        test="test_s0_seed_ingestion",
        verdict="pass",
        observed={"base_datetime": state["meta_base"]},
    )


async def test_s0_l1_unidirectional_match(api, db):
    state = await seed_all(api, db)
    seed = load_seed()
    db_pairs = await fetch_candidates_pairs(db, state["sd_to_uuid"])
    outside = db_pairs - state["l1_pass"]
    assert not outside, (
        f"L1 failペアの生成が{len(outside)}件(無向化突合)。例: {sorted(outside)[:5]}"
    )
    # 代表EX passペアは収束待ち後に実在する(K_v=50余裕・決定的環境)。
    # もし1組でも欠けたら検査を緩めず§0規律どおり作業停止(報告書に実測と考察)
    sample_pairs = {ex_pair_ids(seed, e) for e in state["ex_pass_sample"]}
    missing = {pair for pair in sample_pairs if pair is None or pair not in db_pairs}
    assert not missing, (
        f"代表EX passペア未生成: {sorted(str(p) for p in missing)}"
        "(§0規律どおり作業停止)"
    )
    record_item(
        item_id="02#6-土台",
        name="L1無向突合(生成集合⊆pass集合+代表EX実在)",
        test="test_s0_l1_unidirectional_match",
        verdict="pass",
        observed={
            "pass_pairs": len(state["l1_pass"]),
            "db_pairs": len(db_pairs),
            "sample_ok": len(sample_pairs - missing),
        },
    )


async def test_s0_d08_retention_forecast(api, db):
    state = await seed_all(api, db)
    per = state["pass_pairs_per_user_day"]
    over = {f"{u}@{d}": n for (u, d), n in per.items() if n > 6}
    assert over, "6件超えのユーザー×日が0件 — #8(保留昇格)の材料がない(計画§2.2)"
    top = sorted(over.items(), key=lambda kv: -kv[1])[:5]
    record_item(
        item_id="S0-D08",
        name="D-08保留予測の事前集計(設計§2.2対抗策c)",
        test="test_s0_d08_retention_forecast",
        verdict="pass",
        observed={"over_count": len(over), "top5": top},
    )
