"""実WorkersのD1 bindingをLocalD1Database互換面へ包むwrapper(本体実装)。

実配備probe判明の差異への扱い(docs/testassets/results/cloudflare-prod-probe-20261009.yaml):
- .all()はD1Result{success,meta,results}形式(行は.results内)→ ここで行配列へ正規化
- JsProxy経由の値はstr()/int()で明示変換(json.loads等の型要求)
- .first()はlocalと同一動作のためそのまま透過(行dictの値も正規化)

adapters/d1/*(LocalD1Database前提で書かれている)へ実D1をそのまま渡せる
ようにするための適合層。batchは実D1の原子性(単一transaction)に従う。
"""

from __future__ import annotations

from typing import Any


def _normalize_value(value: Any) -> Any:
    """JsProxy値を素のPython値へ明示変換(probe差異#5)。"""
    if value is None or isinstance(value, (bool, int, float, str)):
        return value
    # JsProxy配列/オブジェクト等: str()して文字列扱い(JSON列は文字列で来る)
    try:
        return str(value)
    except Exception:  # noqa: BLE001
        return value


def _normalize_row(row: Any) -> dict[str, Any]:
    if row is None:
        return {}
    if isinstance(row, dict):
        return {k: _normalize_value(v) for k, v in row.items()}
    # dict()以外のMapping的オブジェクト(D1の行は通常plain object)
    try:
        items = dict(row)
    except Exception:  # noqa: BLE001
        return {"value": _normalize_value(row)}
    return {k: _normalize_value(v) for k, v in items.items()}


class _WrappedStatement:
    """prepare().bind() → first()/all()/run() をawait可能な形へ。"""

    def __init__(self, binding: "RealD1Binding", stmt: Any, params: tuple) -> None:
        self._binding = binding
        self._stmt = stmt
        self._params = params

    def bind(self, *params) -> "_WrappedStatement":
        return _WrappedStatement(self._binding, self._stmt, params)

    async def first(self) -> dict[str, Any] | None:
        bound = self._stmt.bind(*self._params) if self._params else self._stmt
        row = await bound.first()
        return _normalize_row(row) if row is not None else None

    async def all(self) -> list[dict[str, Any]]:
        bound = self._stmt.bind(*self._params) if self._params else self._stmt
        raw = await bound.all()
        # 実D1: D1Result{success, meta, results} / local: 行配列
        if isinstance(raw, list):
            return [_normalize_row(r) for r in raw]
        results = getattr(raw, "results", None)
        if results is None and isinstance(raw, dict):
            results = raw.get("results")
        if results is None:
            return []
        return [_normalize_row(r) for r in results]

    async def run(self):
        bound = self._stmt.bind(*self._params) if self._params else self._stmt
        resp = await bound.run()
        meta = getattr(resp, "meta", None)
        if meta is None and isinstance(resp, dict):
            meta = resp.get("meta", {})
        changes = getattr(meta, "changes", None) if meta is not None else None
        if changes is None and isinstance(meta, dict):
            changes = meta.get("changes", 0)
        return _RunResult(changes=int(changes) if changes is not None else 0)


class _RunResult:
    __slots__ = ("success", "meta")

    def __init__(self, changes: int) -> None:
        self.success = True
        self.meta = {"changes": changes}

    @property
    def changes(self) -> int:
        return int(self.meta.get("changes", 0))


class RealD1Binding:
    """env.DB(実D1)をLocalD1Database互換(prepare/batch)へ適合。"""

    def __init__(self, raw_db: Any) -> None:
        self.raw = raw_db

    def prepare(self, sql: str) -> _WrappedStatement:
        return _WrappedStatement(self, self.raw.prepare(sql), ())

    async def batch(self, statements: list) -> list:
        if not statements:
            return []
        raw_stmts = []
        for s in statements:
            raw = s._stmt  # noqa: SLF001 — wrapper内の閉じた協調
            if s._params:  # noqa: SLF001
                raw = raw.bind(*s._params)
            raw_stmts.append(raw)
        results = await self.raw.batch(raw_stmts)
        out = []
        for resp in results:
            meta = getattr(resp, "meta", None)
            if meta is None and isinstance(resp, dict):
                meta = resp.get("meta", {})
            changes = getattr(meta, "changes", None) if meta is not None else None
            if changes is None and isinstance(meta, dict):
                changes = meta.get("changes", 0)
            out.append(
                _RunResult(changes=int(changes) if changes is not None else 0)
            )
        return out
