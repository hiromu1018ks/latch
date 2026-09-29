"""layer4(選択SQL・H再検証・配分)のunit試験(design §4.1)。"""

import uuid
from datetime import datetime, timedelta

from sqlalchemy.dialects import postgresql

from latch.core.clock import JST
from latch.worker.matching import layer1, layer4
from latch.worker.matching.layer4 import (
    K_J,
    ONE_ON_ONE_MIN,
    JevCandidateRow,
    jst_day_start,
    jst_month_start,
    select_jev_targets,
)


def _row(n: int, kind: str = "one_on_one") -> JevCandidateRow:
    return JevCandidateRow(
        row_id=uuid.UUID(f"00000000-0000-4000-8000-{n:012d}"),
        intent_a_id=uuid.UUID(f"00000000-0000-4000-8000-{n:012d}"),
        intent_b_id=uuid.UUID(f"00000000-0000-4000-8000-{(n + 100):012d}"),
        intent_a_version=1,
        intent_b_version=1,
        cheap_judge_score=0.5,
        status="pending",
        skip_reason=None,
        pair_kind=kind,
    )


def test_constants_pin_docs_values():
    assert K_J == 8  # 06 §8 D-24
    assert ONE_ON_ONE_MIN == 4  # 06 §5(1対1最低保証)


def test_select_targets_identity_and_cap():
    rows = [_row(i) for i in range(8)]
    assert select_jev_targets(rows) == rows  # 恒等(全て1対1)
    rows12 = [_row(i) for i in range(12)]
    assert len(select_jev_targets(rows12)) == 8  # K_J上限
    rows3 = [_row(i) for i in range(3)]
    assert len(select_jev_targets(rows3)) == 3  # 補充しない


def test_select_targets_keeps_input_order():
    """同点順序は入力順維持(並べ替えはSQL側の担当)。"""
    rows = [_row(i) for i in range(6)]
    out = select_jev_targets(rows)
    assert [r.row_id for r in out] == [r.row_id for r in rows]


def test_select_sql_pins():
    sql = str(layer4._SELECT_JEV_ROWS)
    # 選択条件(design §2.5・§2.7)
    assert "mc.jev_result IS NULL" in sql  # FR-07: 同一評価世代スキップのフィルタ
    assert "mc.status = 'pending'" in sql
    assert "'llm_failure', 'invalid_output'" in sql  # 障害系=即時
    assert "'intent_daily', 'user_daily', 'global_daily'" in sql
    assert "mc.skip_reason = 'global_monthly'" in sql
    assert "mc.updated_at < CAST(:jst_day_start AS timestamptz)" in sql
    assert "mc.updated_at < CAST(:jst_month_start AS timestamptz)" in sql
    # 並び替えと上限(LIMITはws-7で撤去 — 配分はselect_jev_targetsが担う)
    assert "ORDER BY mc.cheap_judge_score DESC," in sql
    assert "LIMIT" not in sql.split("ORDER BY", 1)[1]


def test_select_sql_select_columns_pin():
    """SELECT列ピン(JevCandidateRow組立に必要な全列)。"""
    sql = str(layer4._SELECT_JEV_ROWS)
    for col in (
        "mc.id",
        "mc.intent_a_id",
        "mc.intent_b_id",
        "mc.intent_a_version",
        "mc.intent_b_version",
        "mc.cheap_judge_score",
        "mc.status",
        "mc.skip_reason",
    ):
        assert col in sql, col


def test_select_sql_all_bind_params_recognized():
    compiled = str(layer4._SELECT_JEV_ROWS.compile(dialect=postgresql.dialect()))
    for key in ("origin", "origin_version", "jst_day_start", "jst_month_start"):
        assert f":{key}" not in compiled, key


def test_h_recheck_reuses_layer1_where():
    """H再検証はLAYER1_WHEREを再利用(試験と本番のWHERE乖離なし — ws-3規律)。"""
    assert layer1.LAYER1_WHERE.strip() in str(layer4._H_RECHECK)
    sql = str(layer4._H_RECHECK)
    assert "LIMIT 1" in sql
    assert "i.id = CAST(:candidate_id AS uuid)" in sql


def test_close_broken_pins():
    sql = str(layer4._CLOSE_BROKEN)
    assert "jev_result IS NOT NULL" in sql
    assert "mc.status = 'evaluated'" in sql  # evaluated行のみclose(pendingは閉じない)
    assert "SET status = 'closed'" in sql
    assert "NOT EXISTS" in sql and layer1.LAYER1_WHERE.strip() in sql
    compiled = str(layer4._CLOSE_BROKEN.compile(dialect=postgresql.dialect()))
    for key in (
        "origin",
        "origin_version",
        "now",
        "origin_user_id",
        "origin_category",
        "origin_time_start",
        "origin_time_end",
        "origin_lon",
        "origin_lat",
        "origin_radius_m",
        "origin_budget",
        "origin_alcohol",
        "origin_user_ge_20",
    ):
        assert f":{key}" not in compiled, key


def test_jst_boundaries():
    from datetime import date

    d = date(2026, 9, 29)
    assert jst_day_start(d) == datetime(2026, 9, 29, 0, 0, tzinfo=JST)
    assert jst_month_start(d) == datetime(2026, 9, 1, 0, 0, tzinfo=JST)
    # 月跨ぎ・年末
    assert jst_month_start(date(2026, 12, 31)) == datetime(
        2026, 12, 1, 0, 0, tzinfo=JST
    )
    assert jst_day_start(d) + timedelta(hours=24) == jst_day_start(date(2026, 9, 30))


# -- K_j配分とグループ拡張(M2 ws-7・design §2.4) --


def _grp_row(n: int) -> JevCandidateRow:
    return _row(n, kind="group")


def test_select_targets_one_on_one_minimum_four_first():
    """規則1: 1対1上位4件を先に確保(グループが高スコアでも)。"""
    rows = [_row(i) for i in range(6)] + [_grp_row(i) for i in range(20, 24)]
    out = select_jev_targets(rows)
    assert [r.row_id for r in out[:4]] == [r.row_id for r in rows[:4]]
    assert len(out) == 8  # 残り4枠はグループ(新規・rows順)


def test_select_targets_continuing_group_preferred_over_new():
    """規則4: 継続(row_id∉new)→新規(∈)の順。各層内は入力順。"""
    new1, new2 = _grp_row(21), _grp_row(22)
    cont1, cont2 = _grp_row(23), _grp_row(24)
    rows = [_row(1), _row(2), _row(3), _row(4), new1, cont1, new2, cont2]
    out = select_jev_targets(rows, frozenset([new1.row_id, new2.row_id]))
    # 上位4=1対1・残り4枠=継続2件が先・次に新規2件
    assert [r.row_id for r in out] == [
        _row(1).row_id,
        _row(2).row_id,
        _row(3).row_id,
        _row(4).row_id,
        cont1.row_id,
        cont2.row_id,
        new1.row_id,
        new2.row_id,
    ]


def test_select_targets_promotes_one_on_one_when_no_group():
    """規則4: グループ不在は1対1へ繰り上げ(実効上限8)。"""
    rows = [_row(i) for i in range(12)]
    out = select_jev_targets(rows)
    assert [r.row_id for r in out] == [r.row_id for r in rows[:8]]


def test_select_targets_minimum_four_kept_with_continuing():
    """1対1最低4は継続評価があっても確保(引用#6規則4-a)。"""
    cont = [_grp_row(i) for i in range(30, 36)]  # 継続6件(継続優先で食い合う)
    rows = [_row(i) for i in range(6)] + cont
    out = select_jev_targets(rows)  # new_pair_row_ids既定=全グループ継続扱い
    assert sum(1 for r in out if r.pair_kind == "one_on_one") >= 4


def test_select_sql_has_is_group_and_no_limit():
    """is_group列追加・LIMIT撤去(配分は純関数側・design §2.4)。"""
    sql = str(layer4._SELECT_JEV_ROWS)
    assert "AS is_group" in sql
    assert layer4.GROUP_PAIR_EXISTS.strip() in sql
    assert "g.intent_ids @> ARRAY[mc.intent_a_id, mc.intent_b_id]" in sql
    assert "LIMIT" not in sql.split("ORDER BY", 1)[1]


def test_group_pair_exists_fragment_pins():
    frag = layer4.GROUP_PAIR_EXISTS
    assert "group_candidates g" in frag
    assert "g.status IN ('candidate', 'proposed')" in frag
    assert "@>" in frag


def test_h_recheck_group_uses_base_and_relaxed_participants():
    sql = str(layer4._H_RECHECK_GROUP)
    assert layer1.LAYER1_WHERE_BASE.strip()[:40] in sql
    assert "i.participants_min <= 4" in sql
    assert "i.participants_max >= 3" in sql
    assert "i.participants_min <= 2" not in sql  # 人数行は含まない


def test_close_broken_excludes_group_pairs():
    sql = str(layer4._CLOSE_BROKEN)
    assert f"AND NOT {layer4.GROUP_PAIR_EXISTS}" in sql


def test_select_jev_rows_builds_pair_kind():
    """select_jev_rowsがis_group列をpair_kindへ変換(実DBはintegration)。"""
    # unitではSQL文字列ピンのみ。ペア種別変換はgroup_engine試験と
    # integrationで実証する。ここでは定数の存在のみ:
    assert layer4.PAIR_KIND_GROUP == "group"
