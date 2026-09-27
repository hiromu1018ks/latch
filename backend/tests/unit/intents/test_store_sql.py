"""storeのSQL文字列の実dialect検証(実検証で発見した503欠陥の回帰防止)。

test-ci(apiイメージ実ビルド・実HTTP)で POST /v1/intents が全件
503 DEPENDENCY_UNAVAILABLE になった欠陥: _GEO_CENTER_EXPR の
':geo_lon::float8'(bind param の直後の '::' キャスト)を SQLAlchemy の
bind param 正規表現が認識せず、リテラル ':geo_lon::float8' のまま
PostgreSQL へ送られて構文エラーになっていた。unitのスタブconnはSQLを
解釈しないため見逃れた経緯がある(design §4.1の限界)。

ここではスタブではなく実dialectで compile することで「実の形」を模し、
INSERT/UPDATE の全パラメータが bind param として認識されることを保証する。
"""

from sqlalchemy.dialects import postgresql

from latch.intents import store

# _col_params(INSERT/UPDATE共通) + INSERT固有 + UPDATE固有
_INSERT_KEYS = (
    "user_id",
    "category_primary",
    "alcohol_involved",
    "raw_text",
    "structured_data",
    "geo_lon",
    "geo_lat",
    "geo_radius_m",
    "budget_max",
    "participants_min",
    "participants_max",
    "visibility",
    "notification_level",
    "status",
    "version",
    "time_start",
    "time_end",
    "expires_at",
    "created_at",
    "updated_at",
)
_UPDATE_KEYS = (set(_INSERT_KEYS) - {"created_at", "user_id"}) | {
    "intent_id",
    "expected_status",
}


def test_insert_sql_all_bind_params_recognized():
    """compiled文字列に未変換の ':name' が残らない=部分認識ゼロ。

    params集合の検査では不十分: 同名パラメータが別箇所で1度でも認識されれば
    集合に載るため、':name::type' のような未認識箇所を見逃す(今回の欠陥が
    まさにその形)。文字列に ':<key>' が残っていなければ全出現が認識済み。
    """
    compiled = str(store._INSERT.compile(dialect=postgresql.dialect()))
    for key in _INSERT_KEYS:
        assert f":{key}" not in compiled, key


def test_update_sql_all_bind_params_recognized():
    compiled = str(store._UPDATE.compile(dialect=postgresql.dialect()))
    for key in _UPDATE_KEYS:
        assert f":{key}" not in compiled, key


def test_geo_center_expr_has_no_post_colon_cast_param():
    """:name::type の形(認識されない書き方)をSQLへ書かないことのピン留め。"""
    assert "::float8" not in store._GEO_CENTER_EXPR
    assert "CAST(:geo_lon AS float8)" in store._GEO_CENTER_EXPR
    # geographyキャスト(::geography)はパラメータに隣接しないため問題なし
    assert "::geography" in store._GEO_CENTER_EXPR
