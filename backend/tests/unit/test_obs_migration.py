"""0008マイグレーションファイルの静的ピン(M4 ws-4)。

upgrade()の実行(integration)はtest_obs_schema.pyが所有する。unitでは
ファイルの存在・リビジョン連結・定義SQLの骨格のみを検査する。
"""

import importlib.util
from pathlib import Path


def _load_0008():
    path = (
        Path(__file__).resolve().parents[2]
        / "alembic"
        / "versions"
        / "0008_obs_sessions_kpi.py"
    )
    spec = importlib.util.spec_from_file_location("mig_0008", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_revision_chain_points_at_0007():
    mig = _load_0008()
    assert mig.revision == "0008"
    assert mig.down_revision == "0007"


def test_0008_defines_the_three_tables():
    """定義SQL(モジュール定数SQL_STATEMENTS)に3テーブルとINDEXがある。

    FKなし独立表(§2.2(c)・design §4「他テーブルの削除順(FK)に依存しない
    独立表」)の機械確認を兼ねる。
    """
    mig = _load_0008()
    joined = "\n".join(mig.SQL_STATEMENTS)
    for table in ("sessions", "kpi_daily", "kpi_muted_user_snapshot"):
        assert f"CREATE TABLE {table}" in joined
    assert "idx_sessions_user_time" in joined
    assert "REFERENCES" not in joined
