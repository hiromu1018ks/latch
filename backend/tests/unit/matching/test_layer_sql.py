"""Layer 1/2/candidates のSQL文字列ピン+実dialect compile検査(design §4.1)。

test_store_sql.py と test_pubsub_bus_sdk_calls.py の流儀(bind param の
部分認識=リテラル落ちを compile 結果で摘発する。M1 ws-3で503化した欠陥
系統の回帰防止)。ここでは実DBに繋がない(実挙動はintegration)。
"""

from sqlalchemy.dialects import postgresql

from latch.worker.matching import candidates, layer1, layer2

_WHERE_KEYS = (
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
    "now",
)
_LAYER2_KEYS = _WHERE_KEYS + ("origin_embedding",)
_UPSERT_KEYS = (
    "intent_a_id",
    "intent_b_id",
    "intent_a_version",
    "intent_b_version",
    "retrieval_score",
    "cheap_judge_score",
    "now",
)


def test_layer1_where_contains_all_conditions():
    """06 §2・design §2.6確定表の全条件がWHEREに入っている。"""
    where = layer1.LAYER1_WHERE
    assert "i.status = 'active'" in where
    assert "i.embedding IS NOT NULL" in where
    assert "i.user_id <> CAST(:origin_user_id AS uuid)" in where
    assert "i.category_primary = :origin_category" in where
    assert "i.time_start < :origin_time_end" in where
    assert "COALESCE(i.time_end, i.time_start + interval '3 hours')" in where
    assert "ST_DWithin" in where
    assert "LEAST(" in where
    assert ">= 500" in where  # PAIR_BUDGET_MIN_YEN(06 §2の確定値そのまま)
    assert "i.participants_min <= 2" in where
    assert "i.participants_max >= 2" in where
    assert "NOT EXISTS" in where and "blocks" in where
    assert "EXTRACT(YEAR FROM AGE(" in where
    assert "AT TIME ZONE 'Asia/Tokyo'" in where  # JST暦日基準(§9固定値)


def test_layer1_where_excludes_visibility():
    """visibility は判定対象外(06 §2 — Layer 5のproposal分岐が担う)。"""
    assert "visibility" not in layer1.LAYER1_WHERE


def test_layer1_and_layer2_share_the_same_where():
    """design §2.1案A: 02#9単体試験と本番経路(Layer 2)のWHERE乖離なし。"""
    assert layer1.LAYER1_WHERE.strip() in str(layer2._SELECT_TOPK)


def test_hard_filter_sql_has_no_order_no_limit():
    sql = str(layer1._SELECT_HARD)
    assert "ORDER BY" not in sql
    assert "LIMIT" not in sql


def test_layer2_sql_order_limit_and_window():
    """距離ASC・id ASC(同点解消)の複合ORDER BY + LIMIT 50 + 通過件数window。"""
    sql = str(layer2._SELECT_TOPK)
    assert (
        "ORDER BY (i.embedding <=> CAST(:origin_embedding AS vector)) ASC,"
        " i.id ASC" in sql
    )
    assert "LIMIT 50" in sql  # K_VECTORS(D-24)
    assert "COUNT(*) OVER () AS pass_count" in sql
    assert "AS similarity" in sql


def test_layer1_sql_all_bind_params_recognized():
    """compiled文字列に未変換の ':name' が残らない=部分認識ゼロ。"""
    compiled = str(layer1._SELECT_HARD.compile(dialect=postgresql.dialect()))
    for key in _WHERE_KEYS:
        assert f":{key}" not in compiled, key


def test_layer2_sql_all_bind_params_recognized():
    compiled = str(layer2._SELECT_TOPK.compile(dialect=postgresql.dialect()))
    for key in _LAYER2_KEYS:
        assert f":{key}" not in compiled, key


# -- candidates UPSERT のSQLピン(design §2.3) --


def test_upsert_on_conflict_targets_unique_columns():
    sql = str(candidates._UPSERT)
    assert (
        "ON CONFLICT (intent_a_id, intent_b_id,"
        " intent_a_version, intent_b_version)" in sql
    )
    assert "DO UPDATE SET" in sql
    assert "retrieval_score = EXCLUDED.retrieval_score" in sql
    assert "updated_at = EXCLUDED.updated_at" in sql


def test_upsert_do_update_touches_scores_only():
    """DO UPDATE SET は retrieval_score/cheap_judge_score/updated_at のみ
    (statusを壊さない — design §2.3。evaluated/skipped/closedへの遷移は
    ws-5/stage1の担当)。"""
    sql = str(candidates._UPSERT)
    update_clause = sql.split("DO UPDATE SET", 1)[1]
    assert "status" not in update_clause
    assert "cheap_judge_score = EXCLUDED.cheap_judge_score" in update_clause
    # 新規行は status='pending'(05 §2・Layer 1〜3時点でJev未評価)
    insert_part = sql.split("DO UPDATE", 1)[0]
    assert "'pending'" in insert_part


def test_upsert_sql_all_bind_params_recognized():
    compiled = str(candidates._UPSERT.compile(dialect=postgresql.dialect()))
    for key in _UPSERT_KEYS:
        assert f":{key}" not in compiled, key


# -- Layer 3 組込みのSQLピン(M2 ws-4・design §3.2 — 機械的追随) --


def test_layer2_sql_selects_layer3_columns():
    """Layer 3計算に必要な対象側データの列追加(design §3.2)。"""
    sql = str(layer2._SELECT_TOPK)
    assert "i.time_start" in sql
    assert "i.budget_max" in sql
    assert "i.structured_data" in sql


def test_upsert_writes_cheap_judge_score():
    """INSERT列とDO UPDATE句の両方に cheap_judge_score(design §2.3全件記録)。"""
    sql = str(candidates._UPSERT)
    insert_part = sql.split("DO UPDATE", 1)[0]
    assert "cheap_judge_score" in insert_part
    update_clause = sql.split("DO UPDATE SET", 1)[1]
    assert "cheap_judge_score = EXCLUDED.cheap_judge_score" in update_clause


# -- layer1 最小分割(M2 ws-7・design §2.2) --


def test_layer1_split_composes_identical_where():
    """BASE+人数行の分割。LAYER1_WHERE は HEAD+ONE_ON_ONE+TAIL と一致。"""
    assert layer1.LAYER1_WHERE == (
        f"{layer1.LAYER1_WHERE_HEAD}"
        f"{layer1.ONE_ON_ONE_PARTICIPANTS}"
        f"{layer1.LAYER1_WHERE_TAIL}"
    )
    assert layer1.LAYER1_WHERE_BASE == (
        f"{layer1.LAYER1_WHERE_HEAD}{layer1.LAYER1_WHERE_TAIL}"
    )


def test_layer1_base_excludes_participants_conditions():
    """BASEに人数行なし(POOL_SEARCH・_H_RECHECK_GROUPが人数を差し替える)。"""
    assert "participants" not in layer1.LAYER1_WHERE_BASE
    # 人数以外の全条件はBASEにも残る
    where = layer1.LAYER1_WHERE_BASE
    assert "i.status = 'active'" in where
    assert "ST_DWithin" in where
    assert "NOT EXISTS" in where and "blocks" in where
    assert "EXTRACT(YEAR FROM AGE(" in where
    assert "LEAST(" in where


def test_layer1_where_keeps_participants_and_unchanged_pins():
    """合成後のLAYER1_WHEREは人数込み(既存ピンの回帰確認)。"""
    assert "i.participants_min <= 2" in layer1.LAYER1_WHERE
    assert "i.participants_max >= 2" in layer1.LAYER1_WHERE
