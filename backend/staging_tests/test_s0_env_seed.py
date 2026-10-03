"""S0: 環境前提・シード投入・収束待ち・L1無向突合+D-08事前集計(設計§2.3)。

ここが落ちたら後続(S1〜S5)は全て連鎖失敗する — 設計§4-2どおり名前つき
失敗で原因を明示する。
"""

from __future__ import annotations

from datetime import datetime

from helpers_seed import (
    ex_pair_ids,
    fetch_candidates_pairs,
    load_seed,
    seed_all,
    to_reference_view,
)

from conftest import EXPECTED_HEAD, record_item


def _p(value: str) -> datetime:
    return datetime.fromisoformat(value)


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
    # match_candidatesは2経路分の行を含む: ペア検索(LAYER1_WHERE・people条件
    # 込み)と集合検索(LAYER1_WHERE_BASE・people条件除く・layer1.py:58/80)。
    # 期待集合はjudge_allのpass集合に、failペアのうちjudge_pair_group
    # (people除く)で通るものを加えた和集合(計画コードからの適応・報告書に記録)
    from latch.testassets.reference_l1 import judge_pair_group

    users, intents = to_reference_view(seed)
    base = _p(state["meta_base"])
    expected = set(state["l1_pass"])
    for a, b in state["l1_fail"]:
        ia = next(i for i in intents if i["id"] == a)
        ib = next(i for i in intents if i["id"] == b)
        gok, _reason = judge_pair_group(ia, ib, users=users, now=base)
        if gok:
            expected.add((a, b))
    outside = db_pairs - expected
    assert not outside, (
        f"L1外ペアの生成が{len(outside)}件(無向化突合)。例: {sorted(outside)[:5]}"
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


async def test_s0_s20_pair_prepared(api, db):
    """#20用ペア(HS-p20-1/2)の171側IntentをS0で作る(S5のブロック試験の素材)。

    隔離スケジュール(報告書「シナリオ隔離の再設計」): 10-11 06:30はs4-17
    (activity)とカテゴリが異なるdrinking。明示expires_atはClock前進
    (S3でD+6=10-10 00:10)後もIntentをactiveに保ち、S5の新Intentとの
    L1照合がブロック条件そのもので判定されるようにする(既定のnearest
    候補ではS5前に期限切れになり検査が自明化する)。
    """
    from helpers_scenario import make_scenario_pair

    pair = await make_scenario_pair(
        api,
        db,
        key="s5-20",
        name="p20",
        when="2026-10-11T06:30:00+09:00",
        category="drinking",
        secondary="朝の一杯",
        expires_at="2026-10-11T09:00:00+09:00",
        raw_text="早朝に天文館で静かに一杯だけ飲みたい",
    )
    record_item(
        item_id="S0-s20",
        name="#20素材ペア作成(171側のみ・172はS5で作成)",
        test="test_s0_s20_pair_prepared",
        verdict="pass",
        observed={"subjects": pair["subjects"], "intents": [pair["intent_ids"][0]]},
    )
