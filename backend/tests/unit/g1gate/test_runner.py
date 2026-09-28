"""g1gate/runner.py+__main__.pyのunit(design §4-5)。StubLLM応答注入で実Gateway
+実IntentParseService(規則5正規化・ParserOutput検証込み)の全経路を検証。実APIゼロ。"""

from pathlib import Path

import pytest

from latch.core.clock import FakeClock
from latch.g1gate.cases import BASE_CURRENT_DATETIME, load_alcohol, load_parser_struct
from latch.g1gate.compare import summarize_alcohol, summarize_struct
from latch.g1gate.report import build_report, write_report
from latch.g1gate.runner import build_gate_service, run_all
from latch.intents.service import IntentParseService
from latch.llm.gateway import LLMGateway
from latch.llm.stub import StubLLM
from latch.settings import Settings

FIXTURES = Path(__file__).resolve().parents[2] / "fixtures" / "g1gate"

# fixturesのP-901期待値に完全一致する応答(他ケースは一部不一致になる)
STUB_OK_RESPONSE = {
    "category": {"primary": "drinking", "secondary": None},
    "alcohol_involved": True,
    "time": {
        "start": "2026-10-01T20:00:00+09:00",
        "end": None,
        "flexibility_minutes": None,
    },
    "location": {"name": "天文館", "radius_m": None, "flexibility": None},
    "budget": {"max": 3000, "currency": "JPY"},
    "participants": {"min": None, "max": None},
    "soft_constraints": [],
    "negative_constraints": [],
    "ng_unverifiable": [],
}

# 必須3のうち time / location を欠く応答 → ParserOutput検証で422相当
STUB_UNSTRUCTURABLE = {
    "category": {"primary": "drinking", "secondary": None},
    "alcohol_involved": True,
    "budget": {"max": 3000, "currency": "JPY"},
    "participants": {"min": None, "max": None},
    "soft_constraints": [],
    "negative_constraints": [],
    "ng_unverifiable": [],
}


def _service(response: dict | None = None, fail: bool = False) -> IntentParseService:
    """実Gateway(timeout・送信記録込み)+実IntentParseService(本番ロジック)。"""
    stub = StubLLM(parser_response=response, fail_parser=fail)
    gateway = LLMGateway(
        clock=FakeClock(BASE_CURRENT_DATETIME), parser=stub, embedding=stub, jev=stub
    )

    async def _none_lookup(provider: str, subject: str):
        return None  # 未登録JWT経路(design §2.8-A)

    return IntentParseService(
        clock=FakeClock(BASE_CURRENT_DATETIME), parser=gateway, user_lookup=_none_lookup
    )


@pytest.fixture
def struct_set():
    return load_parser_struct(FIXTURES / "struct-minimal.yaml")


@pytest.fixture
def alcohol_set():
    return load_alcohol(FIXTURES / "alcohol-minimal.yaml")


async def test_happy_path_runs_all_three_sets(tmp_path, struct_set, alcohol_set):
    from datetime import datetime

    from latch.core.clock import JST

    service = _service(response=STUB_OK_RESPONSE)
    run = await run_all(service, struct_set, alcohol_set)
    assert not run.incomplete
    assert len(run.struct_outcomes) == 2
    assert len(run.alcohol_outcomes) == 3
    assert len(run.error_outcomes) == 1
    # P-901は完全一致・P-902はtime/location/budgetが不一致(応答はP-901用)
    summary = summarize_struct(run.struct_outcomes)
    assert summary.fields["category"].match == 2  # 両ケースとも期待drinking
    assert summary.fields["time_start"].match == 1
    assert summary.fields["location"].match == 1
    # error_case E-901: STUB_OK応答は422にならない → ok=False(実測では422が出る想定)
    (e,) = run.error_outcomes
    assert e.ok is False
    # alcohol: A-901/A-902(true→true=tp×2)・A-903(false→true=fp×1)
    al = summarize_alcohol(run.alcohol_outcomes)
    assert (al.tp, al.fp, al.fn, al.tn) == (2, 1, 0, 0)
    # レポート生成から書き出しまで(tmp_path)
    executed_at = datetime(2026, 9, 28, 12, 0, tzinfo=JST)
    report = build_report(
        executed_at=executed_at,
        model="claude-haiku-4-5",
        llm_mode="stub",
        parser_prompt_sha256="x",
        input_sets={},
        base_current_datetime=BASE_CURRENT_DATETIME.isoformat(),
        limit=None,
        struct=summary,
        struct_cases=run.struct_outcomes,
        alcohol_reference={"tp": 2, "fp": 0, "fn": 0, "tn": 0},
        alcohol=al,
        alcohol_cases=run.alcohol_outcomes,
        error_results=run.error_outcomes,
        incomplete=run.incomplete,
    )
    path = write_report(report, out_dir=tmp_path, executed_at=executed_at)
    assert path.exists()


async def test_unstructurable_response_yields_422_outcomes(struct_set, alcohol_set):
    service = _service(response=STUB_UNSTRUCTURABLE)
    run = await run_all(service, struct_set, alcohol_set)
    assert not run.incomplete  # 422は正当な応答(実行不完全ではない)
    assert all(
        o.actual is None and o.error == "unstructurable" for o in run.struct_outcomes
    )
    (e,) = run.error_outcomes
    assert e.ok is True  # E-901は422期待どおり
    assert e.actual == "422 VALIDATION_ERROR"
    # alcohol側: 出力なし → 正例はFN・負例はunclassified
    al = summarize_alcohol(run.alcohol_outcomes)
    assert al.unclassified == 1
    assert al.passed is False


async def test_llm_unavailable_makes_run_incomplete(struct_set, alcohol_set):
    # Review Focus #2: 503系が1件でもあれば実行不完全(分母を減らして集計しない)
    service = _service(fail=True)
    run = await run_all(service, struct_set, alcohol_set)
    assert run.incomplete
    assert all(o.error == "llm_unavailable" for o in run.struct_outcomes)
    (e,) = run.error_outcomes
    assert e.actual == "503 LLM_UNAVAILABLE" and e.ok is False


async def test_rule5_normalization_applies_via_real_service(struct_set, alcohol_set):
    # design §4-5: negative_constraints非空の応答でも本番ロジック経由で
    # ng_unverifiableへ移る(規則5正規化の通過証明)
    response = {**STUB_OK_RESPONSE, "negative_constraints": ["会社関係の人は避けたい"]}
    service = _service(response=response)
    run = await run_all(service, struct_set, alcohol_set, limit=1)
    (o,) = run.struct_outcomes
    assert o.actual["negative_constraints"] == []
    assert o.actual["ng_unverifiable"] == ["会社関係の人は避けたい"]


async def test_limit_runs_subset_per_set(struct_set, alcohol_set):
    service = _service(response=STUB_OK_RESPONSE)
    run = await run_all(service, struct_set, alcohol_set, limit=1)
    assert len(run.struct_outcomes) == 1
    assert len(run.alcohol_outcomes) == 1
    assert len(run.error_outcomes) == 1


async def test_echo_reports_progress(struct_set, alcohol_set):
    lines: list[str] = []
    service = _service(response=STUB_OK_RESPONSE)
    await run_all(service, struct_set, alcohol_set, limit=1, echo=lines.append)
    assert lines == ["P-901: ok", "A-901: ok", "E-901: ok"]


def test_build_gate_service_with_stub_settings(monkeypatch):
    monkeypatch.delenv("LATCH_LLM_MODE", raising=False)
    monkeypatch.delenv("LATCH_ANTHROPIC_API_KEY", raising=False)
    service = build_gate_service(Settings())
    assert isinstance(service, IntentParseService)


def test_build_gate_service_real_requires_key(monkeypatch):
    # 鍵なしrealはfail-fast(make_intent_parse_service→build_llm_gateway経由)
    monkeypatch.delenv("LATCH_LLM_MODE", raising=False)
    monkeypatch.delenv("LATCH_ANTHROPIC_API_KEY", raising=False)
    with pytest.raises(ValueError):
        build_gate_service(Settings(llm_mode="real"))


def test_main_rejects_stub_mode(monkeypatch, capsys):
    # design §2.7: 起動検証 — stubのまま実測したと錯覚させない
    monkeypatch.delenv("LATCH_LLM_MODE", raising=False)
    monkeypatch.delenv("LATCH_ANTHROPIC_API_KEY", raising=False)
    from latch.g1gate.__main__ import main

    assert main([]) == 2
    assert "LATCH_LLM_MODE" in capsys.readouterr().err


def test_main_rejects_missing_key(monkeypatch, capsys):
    monkeypatch.setenv("LATCH_LLM_MODE", "real")
    monkeypatch.delenv("LATCH_ANTHROPIC_API_KEY", raising=False)
    from latch.g1gate.__main__ import main

    assert main([]) == 2
    assert "LATCH_ANTHROPIC_API_KEY" in capsys.readouterr().err
