"""API契約パリティ試験(T0・参照実装 = PostgreSQL版)。

backend/tests/contracts/cases.py のケースを参照実装(compose常設api)
へ実行し、HTTP契約(route・status・error code・応答形状)を固定する。

- 移行実装(D1/Workers)は同じケース集合を流してパリティを取る
  (実行方法は実装側のconftestが提供する)。
- 深い業務効果(match_events・calibrationの中身・削除cascade)は既存
  integration試験が参照実装の担保として持ち、ここではHTTP面のみ。
- BaselineManifest は「比較基準が動いていないか」の検出に使う
  (prompt・model・limits・fixture hash)。
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent))
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from contracts import contract as contract_mod  # noqa: E402
from contracts.cases import CONTRACT_CASES, PLANNED_REGRESSION_CASES  # noqa: E402
from contracts.reference_harness import ReferenceHarness  # noqa: E402
from contracts.runner import ReferenceTransport, run_case  # noqa: E402

pytestmark = pytest.mark.integration


@pytest.fixture
async def harness(api_client, db_engine, migrated_db):
    h = ReferenceHarness(api_client, db_engine)
    yield h
    await h.cleanup()


@pytest.fixture
async def transport(api_client):
    return ReferenceTransport(api_client)


@pytest.mark.parametrize("case", CONTRACT_CASES, ids=[c.case_id for c in CONTRACT_CASES])
async def test_contract_case(case, harness, transport):
    variables = {**harness.clock_vars(), **await harness.prepare_case_vars(case)}
    problems = await run_case(
        case,
        transport,
        resolve_token=harness.resolve_token,
        seed_fn=harness.seed,
        variables=variables,
    )
    assert not problems, "\n".join(problems)


def test_regression_cases_are_declared_not_executed():
    """将来回帰ケースは4分類すべてを持ち・参照実装では実行しない。

    T0要件: SQL fakeでは見逃す系統をケースとして定義しておく。
    バグを正解fixtureにしない(期待挙動を固定する)。
    """
    categories = {c.regression for c in PLANNED_REGRESSION_CASES}
    assert categories == set(contract_mod.REGRESSION_CATEGORIES)
    for case in PLANNED_REGRESSION_CASES:
        assert case.planned_for, f"{case.case_id} は担当タスク未指定"
        assert not case.executes_on_reference


def test_baseline_manifest_current():
    """保存済みBaselineManifestと現行の差分がないこと。

    差分がある = 比較基準(prompt・model・limits・schema・fixture)が動いた。
    code_revision は記録値(どのcommitで基準を採取したか)なので比較から
    除く — 比較対象は基準の「中身」。
    意図した変更なら `PYTHONPATH=tests uv run python -m contracts.regen_manifest`
    で更新する。
    """
    stored = contract_mod.load_stored_manifest()
    assert stored is not None, (
        "baseline_manifest.yaml が無い。python -m contracts.regen_manifest で生成"
    )
    current = contract_mod.build_baseline_manifest()
    drift = contract_mod.manifest_drift(stored, current)
    drift = [d for d in drift if not d.startswith("manifest.code_revision")]
    assert not drift, "基準が動いた: " + "; ".join(drift)


def test_case_coverage_by_area():
    """主要API領域をケースが網羅していること(T0完了条件の自己検査)。"""
    areas = {c.area for c in CONTRACT_CASES}
    expected = {
        "auth", "users", "consent", "intents.parse", "intents.crud", "expiry",
        "latches", "chat", "attendance", "safety", "notifications", "account",
        "ratelimit",
    }
    missing = expected - areas
    assert not missing, f"未カバー領域: {missing}"
