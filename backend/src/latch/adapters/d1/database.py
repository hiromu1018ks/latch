"""D1 API互換のローカル実装(T3: ローカルbinding試験用)。

Cloudflare D1 Worker API と同じ操作面を持たせる:
  db.prepare(sql) → stmt; stmt.bind(*params) → bound
  await bound.first() / all() / run()
  await db.batch([stmt, ...])   # 原子的(1 transaction・失敗時すべてrollback)

バックエンドはSQLite(stdlib)。batchは単一transactionとして実行する
ため、途中失敗で副作用が部分保存されないことを試験で検証できる
(計画T3: 「0行UPDATE、途中SQL失敗、再配送で、副作用が部分保存されない
実D1試験を先に作る」— 同一SQL・同一意味論をローカルで先に担保)。

注意: この実装はテスト用の単一接続・単一プロセス前提。実Workersの
D1は分散するため、並行挙動の最終確認は実D1で行う(別承認後)。
"""

from __future__ import annotations

import json
import sqlite3
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

_MIGRATIONS_DIR = Path(__file__).resolve().parents[5] / "cloudflare" / "migrations"


@dataclass
class D1Response:
    """D1のrun()/batch()結果(meta.changes は影響行数)。"""

    success: bool = True
    meta: dict[str, Any] = field(default_factory=dict)

    @property
    def changes(self) -> int:
        return int(self.meta.get("changes", 0))


class D1PreparedStatement:
    """prepare(sql).bind(*params)。first/all/run は await 可能。"""

    def __init__(self, db: LocalD1Database, sql: str, params: tuple = ()) -> None:
        self._db = db
        self._sql = sql
        self._params = params

    def bind(self, *params) -> D1PreparedStatement:
        return D1PreparedStatement(self._db, self._sql, params)

    def _execute(self) -> sqlite3.Cursor:
        return self._db._raw_execute(self._sql, self._params)

    def _rows_as_dicts(self, cur: sqlite3.Cursor) -> list[dict[str, Any]]:
        cols = [d[0] for d in cur.description] if cur.description else []
        return [dict(zip(cols, row, strict=False)) for row in cur.fetchall()]

    # -- awaitable API(D1と同一形状) --

    async def first(self) -> dict[str, Any] | None:
        cur = self._execute()
        rows = self._rows_as_dicts(cur)
        return rows[0] if rows else None

    async def all(self) -> list[dict[str, Any]]:
        return self._rows_as_dicts(self._execute())

    async def run(self) -> D1Response:
        cur = self._execute()
        return D1Response(
            success=True,
            meta={"changes": cur.rowcount, "last_row_id": cur.lastrowid},
        )


class LocalD1Database:
    """D1互換のローカルDB(単一connection・ForeignKeys ON)。"""

    def __init__(self, path: str = ":memory:") -> None:
        self._conn = sqlite3.connect(path, isolation_level=None)
        self._conn.execute("PRAGMA foreign_keys = ON")
        self._conn.execute("PRAGMA journal_mode = MEMORY")
        self.journal_mode_dbg = self._conn.execute("PRAGMA foreign_keys").fetchone()

    # -- migration --

    def migrate(self) -> None:
        """cloudflare/migrations/*.sql を順番に適用(冪等: IF NOT EXISTS)。"""
        for sql_file in sorted(_MIGRATIONS_DIR.glob("*.sql")):
            self._conn.executescript(sql_file.read_text(encoding="utf-8"))

    # -- D1 API --

    def prepare(self, sql: str) -> D1PreparedStatement:
        return D1PreparedStatement(self, sql)

    async def batch(self, statements: list[D1PreparedStatement]) -> list[D1Response]:
        """準備済みSQL群を原子的に実行(§3.2: D1 batch の意味論)。"""
        if not statements:
            return []
        results: list[D1Response] = []
        self._conn.execute("BEGIN IMMEDIATE")
        try:
            for stmt in statements:
                cur = self._conn.execute(stmt._sql, stmt._params)
                results.append(
                    D1Response(
                        success=True,
                        meta={"changes": cur.rowcount, "last_row_id": cur.lastrowid},
                    )
                )
            self._conn.execute("COMMIT")
        except Exception:
            self._conn.execute("ROLLBACK")
            raise
        return results

    # -- 内部(直接実行・batch外の読取にも使う) --

    def _raw_execute(self, sql: str, params: tuple) -> sqlite3.Cursor:
        return self._conn.execute(sql, params)

    async def exec(self, sql: str, params: tuple = ()) -> D1Response:
        cur = self._raw_execute(sql, params)
        return D1Response(success=True, meta={"changes": cur.rowcount})

    # -- test helpers --

    def dump_table(self, table: str) -> list[dict[str, Any]]:
        cur = self._conn.execute(f"SELECT * FROM {table}")  # noqa: S608
        cols = [d[0] for d in cur.description]
        return [dict(zip(cols, row, strict=False)) for row in cur.fetchall()]

    def close(self) -> None:
        self._conn.close()


def canonical_json(value: Any) -> str:
    """D1保存用の正規JSON(一意制約がかかる列はこの形式で統一)。"""
    return json.dumps(value, ensure_ascii=True, separators=(",", ":"))
