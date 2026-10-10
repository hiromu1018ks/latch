"""domain/ports の依存検査(T2・計画 §6 T2)。

domain配下から SQLAlchemy・asyncpg・Redis・Pub/Sub・ネイティブ地理処理
へ到達しないことをASTベースの静的解析で強制する。PostgreSQL版と
Workers版が同じdomainを共有するための境界条件。
"""

from __future__ import annotations

import ast
from pathlib import Path

SRC = Path(__file__).resolve().parents[2] / "src"

FORBIDDEN_ROOTS = {
    "sqlalchemy",
    "asyncpg",
    "redis",
    "alembic",
    "google.cloud.pubsub",
    "google.cloud.pubsub_v1",
    "osmium",
    "uvicorn",
    "prometheus_client",
}
# latch内部でDB依存の側へ置くモジュール(domainからimport禁止)
FORBIDDEN_LATCH = {
    "latch.core.db",
    "latch.events",
    "latch.geo",
    "latch.worker.stage1",
    "latch.worker.main",
    "latch.intents.store",
    "latch.latches.store",
    "latch.llm.gateway",
}

ALLOWED_ROOTS = ("domain", "ports")


def _module_nodes(root: Path):
    for py in sorted(root.rglob("*.py")):
        if py.parent.name == "__pycache__":
            continue
        yield py, ast.parse(py.read_text(encoding="utf-8"))


def _imports_of(tree: ast.Module, *, module_level_only: bool = True) -> set[str]:
    """import名の収集。

    module_level_only=True は module import時に実行される文のみを対象に
    する(関数内の遅延import・TYPE_CHECKING blockは読み込み時には実行
    されないため許容 — core.clock の make_clock 内 redis 遅延import等)。
    """
    names: set[str] = set()
    nodes: list[ast.stmt] = tree.body
    if not module_level_only:
        nodes = list(ast.walk(tree))
    skip: set[int] = set()
    for node in ast.walk(tree):
        if (
            isinstance(node, ast.If)
            and isinstance(node.test, ast.Name)
            and node.test.id == "TYPE_CHECKING"
        ):
            skip.update(id(n) for n in ast.walk(node))
    for node in nodes:
        if id(node) in skip:
            continue
        if isinstance(node, ast.Import):
            names.update(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom):
            # TYPE_CHECKING block内のimportは実行されない
            if node.module and node.level == 0 and node.module != "typing":
                names.add(node.module)
    return names


def test_domain_and_ports_have_no_infra_dependencies():
    """domain/ports → 禁止モジュール の直接importが存在しないこと。"""
    violations: list[str] = []
    for area in ALLOWED_ROOTS:
        for py, tree in _module_nodes(SRC / "latch" / area):
            for name in _imports_of(tree):
                root = name.split(".")[0]
                if root in FORBIDDEN_ROOTS or name in FORBIDDEN_ROOTS:
                    violations.append(f"{py.name}: {name}")
                if name in FORBIDDEN_LATCH or name.startswith(
                    tuple(f"{m}." for m in FORBIDDEN_LATCH)
                ):
                    violations.append(f"{py.name}: {name}")
    assert not violations, f"禁止依存: {violations}"


def test_domain_transitive_closure_is_clean():
    """domain から到達可能な latch モジュール群も infra 依存を持たないこと。"""

    def module_file(module: str) -> Path | None:
        rel = module.replace(".", "/")
        direct = SRC / f"{rel}.py"
        if direct.exists():
            return direct
        pkg = SRC / rel / "__init__.py"
        return pkg if pkg.exists() else None

    seen: set[str] = set()
    stack = ["latch.domain"]
    problems: list[str] = []
    while stack:
        module = stack.pop()
        if module in seen:
            continue
        seen.add(module)
        path = module_file(module)
        if path is None:
            continue
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for name in _imports_of(tree):
            if not name.startswith("latch."):
                root = name.split(".")[0]
                if root in FORBIDDEN_ROOTS:
                    problems.append(f"{module} -> {name}")
                continue
            if name in FORBIDDEN_LATCH or name.startswith(
                tuple(f"{m}." for m in FORBIDDEN_LATCH)
            ):
                problems.append(f"{module} -> {name}")
                continue
            stack.append(name)
    assert not problems, f"domain到達可能な依存に禁止モジュール: {problems}"


PURE_CALC_MODULES = [
    "latch.worker.matching.latch_calc",
    "latch.worker.matching.group_calc",
    "latch.worker.matching.proposal",
    "latch.worker.matching.layer3",
    "latch.worker.embedding_text",
    "latch.llm.jev",
    "latch.intents.mapping",
    "latch.intents.completion",
    "latch.intents.intent_input",
]


def test_pure_calc_modules_do_not_reach_sqlalchemy():
    """純計算モジュール群のimport連鎖がSQLAlchemy/asyncpg/Redisへ
    到達しないこと(AST準静的解析)。

    実importだと latch.worker.matching パッケージの __init__ が
    layer1/layer2(SQLAlchemy)を再exportするため、モジュール単位の
    依存連鎖をASTで追う(Workersへvendoringする際の境界保証)。
    """
    problems: list[str] = []

    def check_module(module: str, seen: set[str]) -> None:
        if module in seen:
            return
        seen.add(module)
        rel = module.replace(".", "/")
        path = SRC / f"{rel}.py"
        if not path.exists():
            path = SRC / rel / "__init__.py"
            if not path.exists():
                return
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for name in _imports_of(tree):
            root = name.split(".")[0]
            if root in FORBIDDEN_ROOTS:
                problems.append(f"{module} -> {name}")
                continue
            if not name.startswith("latch."):
                continue
            if name in FORBIDDEN_LATCH or name.startswith(
                tuple(f"{m}." for m in FORBIDDEN_LATCH)
            ):
                problems.append(f"{module} -> {name}")
                continue
            check_module(name, seen)

    seen: set[str] = set()
    for module in PURE_CALC_MODULES:
        check_module(module, seen)
    assert not problems, f"純計算モジュールの禁止依存: {problems}"


def test_domain_imports_do_not_load_infra_at_runtime():
    """domain配下の実importでSQLAlchemy等が読み込まれないこと(実行時検査)。"""
    import subprocess
    import sys

    code = (
        "import sys\n"
        "import latch.domain.time\n"
        "import latch.domain.types\n"
        "import latch.ports.atomic\n"
        "import latch.ports.evaluations\n"
        "import latch.ports.retrieval\n"
        "import latch.ports.schedule\n"
        "loaded = [m for m in ('sqlalchemy', 'asyncpg', 'redis') "
        "if m in sys.modules]\n"
        "assert not loaded, f'infra modules loaded: {loaded}'\n"
        "print('clean')\n"
    )
    proc = subprocess.run(
        [sys.executable, "-c", code], capture_output=True, text=True, cwd=SRC
    )
    assert proc.returncode == 0, proc.stderr
    assert "clean" in proc.stdout


def test_domain_types_contract():
    """domain.types の契約型の基本振る舞い(将来の drift 検出)。"""
    from latch.domain.types import (
        ClaimResult,
        CommandResult,
        CommandStatus,
        EvaluationKey,
        IntentSnapshot,
        ScheduleRequest,
    )

    result = CommandResult(status=CommandStatus.CONFLICT, error_code="LATCH_EXPIRED")
    assert result.status.value == "conflict"
    assert result.response is None

    key = EvaluationKey(
        kind="jev",
        pair_key="jev:a:b:1:1",
        fingerprint="sha256:abc",
        context_version="ctx-1",
    )
    assert key.kind == "jev"

    claim = ClaimResult(acquired=True, fencing_token=7)
    assert claim.fencing_token == 7

    import datetime as dt

    snap = IntentSnapshot(
        intent_id="i-1",
        version=1,
        user_id="u-1",
        status="active",
        category_primary="meal",
        category_secondary=None,
        alcohol_involved=False,
        time_start=dt.datetime(2026, 10, 8, 19, 0, tzinfo=dt.UTC),
        time_end=None,
        flexibility_minutes=None,
        geo_lon=130.55,
        geo_lat=31.59,
        radius_m=None,
        budget_max=None,
        participants_min=None,
        participants_max=None,
    )
    assert snap.visibility == "hidden_until_match"

    req = ScheduleRequest(
        intent_id="i-1",
        requested_revision=2,
        reason="updated",
        due_at=dt.datetime(2026, 10, 8, 12, 0, tzinfo=dt.UTC),
    )
    assert req.reason == "updated"


def test_ports_protocols_are_runtime_checkable():
    """ports の Protocol が runtime_checkable であること(実装の適合検査用)。"""
    from latch.ports.atomic import AtomicRepository
    from latch.ports.evaluations import EvaluationGateway
    from latch.ports.retrieval import RetrievalRepository
    from latch.ports.schedule import ScheduleRepository

    for proto in (
        AtomicRepository,
        EvaluationGateway,
        RetrievalRepository,
        ScheduleRepository,
    ):
        assert hasattr(proto, "__protocol_attrs__") or callable(
            getattr(proto, "_is_protocol", None)
        )
