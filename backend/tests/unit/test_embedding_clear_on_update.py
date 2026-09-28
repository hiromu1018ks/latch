"""内容更新時のembeddingクリア(design §2.3・§4.1)。

_UPDATEは全置換UPDATEのため、SET句のNULL化が全呼び出し経路で一貫して
作用すること(active更新→クリア/draft→no-op)と、resume経路
(_UPDATE_STATUS)ではクリアされないこと(06 §9のresume規定)を検証する。
"""

import uuid
from datetime import UTC, datetime

from sqlalchemy.dialects import postgresql

from latch.intents import store
from latch.intents.mapping import ResolvedColumns


def test_update_sql_clears_embedding_columns():
    """_UPDATEのSET句がembedding/embedding_modelをNULL化する(SQL文字列ピン)。"""
    compiled = str(store._UPDATE.compile(dialect=postgresql.dialect()))
    assert "embedding = NULL" in compiled
    assert "embedding_model = NULL" in compiled


def test_update_status_sql_does_not_clear_embedding():
    """resume(pause/resume/delete)経路の_UPDATE_STATUSはクリアしない。"""
    compiled = str(store._UPDATE_STATUS.compile(dialect=postgresql.dialect()))
    assert "embedding" not in compiled


class _RecordingConn:
    """IntentStore.updateが実行するSQLを記録するスタブ。"""

    def __init__(self):
        self.executed: list[tuple[str, dict | None]] = []

    async def execute(self, stmt, params=None):
        self.executed.append((str(stmt), params))

        class _R:
            rowcount = 1

        return _R()


async def test_store_update_executes_clearing_sql():
    """IntentStore.update の実行SQLにもNULL化句が含まれる(スタブconn検証)。"""
    conn = _RecordingConn()
    st = store.IntentStore(engine=None)
    await st.update(
        conn,
        uuid.uuid4(),
        ResolvedColumns(),
        status="active",
        version=2,
        now=datetime(2026, 9, 29, 12, 0, 0, tzinfo=UTC),
        expected_status="active",
    )
    sql = conn.executed[0][0]
    assert "embedding = NULL" in sql
    assert "embedding_model = NULL" in sql
