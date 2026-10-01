"""削除カスケード(intents/deletion.py)のunit試験(M3 ws-6 design §2.1・§4.1)。

スタブconnでSQL実行順序(FK依存の固定順)とbindパラメータをピンする。
実DBでの削除結果(①〜⑥の検証)は integration/test_deletion_api.py が担う
(§9-6の適合措置 — unitはDBレス規律)。
"""

import uuid
from datetime import UTC, datetime

from sqlalchemy.dialects import postgresql

from latch.intents import deletion

NOW = datetime(2026, 10, 1, 12, 0, 0, tzinfo=UTC)
IID = uuid.UUID("00000000-0000-4000-8000-0000000000aa")
OTHER = uuid.UUID("00000000-0000-4000-8000-0000000000cc")
LID = uuid.UUID("00000000-0000-4000-8000-0000000000dd")


class FakeResult:
    def __init__(self, row=None, rowcount=0):
        self._row = row
        self.rowcount = rowcount

    def first(self):
        return self._row

    def fetchall(self):
        return [self._row] if self._row else []


class ScriptedConn:
    def __init__(self, results: list):
        self.calls: list[tuple[str, dict | None]] = []
        self._results = list(results)

    async def execute(self, stmt, params=None):
        self.calls.append((str(stmt), params))
        if self._results:
            return self._results.pop(0)
        return FakeResult(None, 0)


def _sql(conn, i: int) -> str:
    return conn.calls[i][0]


def _no_row_results() -> list:
    """cascade全体の応答列(候補・latches・calibrationすべて0行/0件)。"""
    return [
        FakeResult(None, 0),  # ① match_candidates DELETE
        FakeResult(None, 0),  # ② latches group_candidate_id NULL
        FakeResult(None, 0),  # ③ group_candidates DELETE
        FakeResult((), 0),  # ④ 開いているlatches SELECT(0行)
        FakeResult((), 0),  # ④ matched latches SELECT(0行)
        FakeResult(None, 0),  # ⑤ calibration匿名化
        FakeResult(None, 0),  # ⑥ intents DELETE
    ]


async def test_cascade_sql_order_and_params():
    """FK依存の固定順: ①候補→②latches FK解消→③group→④latches→⑤匿名化→⑥Intent。"""
    conn = ScriptedConn(_no_row_results())
    await deletion.cascade_delete_intent(conn, IID, NOW)
    # ① 処理済み含む全status(WHEREにstatus条件なし)
    sql1 = _sql(conn, 0)
    assert "DELETE FROM match_candidates" in sql1
    assert "intent_a_id = CAST(:intent_id AS uuid)" in sql1
    assert "intent_b_id = CAST(:intent_id AS uuid)" in sql1
    assert "status" not in sql1
    assert conn.calls[0][1] == {"intent_id": IID}
    # ② FK解消が③より前(RESTRICT回避)
    sql2 = _sql(conn, 1)
    assert "UPDATE latches" in sql2 and "group_candidate_id = NULL" in sql2
    assert "ANY(intent_ids)" in sql2
    # ③ group削除
    assert "DELETE FROM group_candidates" in _sql(conn, 2)
    # ⑤ 匿名化・⑥ Intent行(raw_text・embeddingごと)
    assert "UPDATE calibration_records" in _sql(conn, 5)
    assert "DELETE FROM intents" in _sql(conn, 6)
    assert conn.calls[6][1] == {"intent_id": IID}


async def test_cascade_matched_latch_dissolve_and_restore():
    """matched行→cancelled+イベント(user_id=NULL)+残存Intent復帰(削除対象を除く)。"""
    conn = ScriptedConn(
        [
            FakeResult(None, 0),  # match DELETE
            FakeResult(None, 0),  # detach
            FakeResult(None, 0),  # group DELETE
            FakeResult((), 0),  # open SELECT 0行
            FakeResult((LID, (IID, OTHER)), 0),  # matched SELECT 1行
            FakeResult((LID,), 1),  # cancel matched(RETURNING)
            FakeResult(None, 0),  # latch_status_events INSERT
            FakeResult((OTHER,), 1),  # 復帰 UPDATE RETURNING
            FakeResult(None, 0),  # calibration
            FakeResult(None, 0),  # intents DELETE
        ]
    )
    await deletion.cascade_delete_intent(conn, IID, NOW)
    assert "status = 'matched'" in _sql(conn, 4)  # 対象はmatched行のみ
    assert "'cancelled'" in _sql(conn, 5)
    ev = conn.calls[6][1]
    assert ev["from_status"] == "matched"
    assert ev["to_status"] == "cancelled"
    assert ev["user_id"] is None  # システム起因
    restore_sql = _sql(conn, 7)
    assert "CASE WHEN expires_at <= CAST(:now AS timestamptz)" in restore_sql
    assert conn.calls[7][1]["ids"] == [OTHER]  # 削除対象IIDは除外


async def test_cascade_open_latches_cancel_with_events():
    """開いているlatches(candidate/proposed/partial_accept)→cancelled+イベント。"""
    conn = ScriptedConn(
        [
            FakeResult(None, 0),
            FakeResult(None, 0),
            FakeResult(None, 0),
            FakeResult((LID, "proposed"), 0),  # open SELECT
            FakeResult((LID,), 1),  # cancel(RETURNING)
            FakeResult(None, 0),  # latch_status_events INSERT
            FakeResult((), 0),  # matched SELECT 0行
            FakeResult(None, 0),
            FakeResult(None, 0),
        ]
    )
    await deletion.cascade_delete_intent(conn, IID, NOW)
    assert "IN ('candidate', 'proposed', 'partial_accept')" in _sql(conn, 3)
    ev = conn.calls[5][1]
    assert ev["from_status"] == "proposed"
    assert ev["to_status"] == "cancelled"
    assert ev["user_id"] is None


async def test_cascade_idempotent_second_run_no_rows():
    """二重実行: 全操作が0行/空でも例外なく完了(design §2.2の冪等)。"""
    conn = ScriptedConn(_no_row_results() + _no_row_results())
    await deletion.cascade_delete_intent(conn, IID, NOW)
    await deletion.cascade_delete_intent(conn, IID, NOW)  # 例外なし
    assert len(conn.calls) == 14  # 2周×7本


async def test_anonymize_sql_pins():
    """D-13第一段のSQLピン: user_id除去・順序保存・冪等ガード・anonymized_at不変。"""
    sql = str(deletion._ANONYMIZE_CALIBRATION)
    assert "latch_id = NULL" in sql
    assert "intent_ids = NULL" in sql
    assert "e - 'user_id'" in sql
    assert "WITH ORDINALITY" in sql  # 配列順保存
    assert "jsonb_agg" in sql and "COALESCE" in sql
    assert "latch_id IS NOT NULL" in sql  # 匿名化済み行スキップ
    assert "= ANY(intent_ids)" in sql
    assert "anonymized_at" not in sql  # 第二段まで立てない


def test_all_sql_bind_params_recognized():
    """実dialectでcompileし未認識 ':name' が残らない(test_store_sql流儀)。"""
    names = (
        "_DELETE_MATCH_CANDIDATES",
        "_DETACH_GROUP_CANDIDATES",
        "_DELETE_GROUP_CANDIDATES",
        "_ANONYMIZE_CALIBRATION",
        "_DELETE_INTENT",
        "_SELECT_OPEN_LATCHES_ON_DELETE",
        "_CANCEL_LATCH_ON_DELETE",
        "_SELECT_MATCHED_LATCHES_ON_DELETE",
        "_CANCEL_MATCHED_LATCH_ON_DELETE",
        "_RESTORE_INTENTS_ON_DISSOLVE",
        "_INSERT_LATCH_EVENT_SQL",
    )
    keys = (
        "intent_id",
        "now",
        "latch_id",
        "from_status",
        "to_status",
        "user_id",
        "ids",
    )
    for name in names:
        compiled = str(getattr(deletion, name).compile(dialect=postgresql.dialect()))
        for key in keys:
            assert f":{key}" not in compiled, (name, key)
