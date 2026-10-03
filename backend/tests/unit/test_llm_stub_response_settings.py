"""stub Jev判定値のsettings固定経路(M4 ws-3・10 §3表staging行)。

「Jevスタブで双方向判定値を固定」のenv→settings→StubLLM応答の経路を
機械ピンする。既定値はstub.py DEFAULT_JEV_RESPONSEと値が完全一致する
(回帰ピン — ci/test-ci挙動不変の根拠・design §2.2)。
"""

from datetime import UTC, datetime

from latch.core.clock import FakeClock
from latch.llm.gateway import (
    _stub_jev_response,
    build_llm_gateway,
    build_worker_gateway,
)
from latch.llm.stub import DEFAULT_JEV_RESPONSE
from latch.settings import Settings

LLM_ENV_VARS = (
    "LATCH_LLM_MODE",
    "LATCH_LLM_STUB_DELAY_PARSER_MS",
    "LATCH_LLM_STUB_DELAY_EMBEDDING_MS",
    "LATCH_LLM_STUB_DELAY_JEV_MS",
    "LATCH_LLM_STUB_JEV_NOUL",
    "LATCH_LLM_STUB_JEV_SCORE",
    "LATCH_ANTHROPIC_API_KEY",
    "LATCH_ANTHROPIC_BASE_URL",
    "LATCH_GEMINI_API_KEY",
    "LATCH_TYPESAFE_API_KEY",
    "LATCH_TYPESAFE_BASE_URL",
)

NOUL_KEYS = ("would_a_accept_b", "would_b_accept_a", "latent_yes")
SCORE_KEYS = ("purpose_fit", "mood_fit", "timing_fit", "social_fit")


def _clean_settings(monkeypatch, **overrides) -> Settings:
    for var in LLM_ENV_VARS:
        monkeypatch.delenv(var, raising=False)
    return Settings(**overrides)


def test_settings_default_matches_stub_constant(monkeypatch):
    """既定値で組んだ応答がDEFAULT_JEV_RESPONSEと完全一致(ci挙動不変の回帰ピン)。"""
    s = _clean_settings(monkeypatch)
    assert _stub_jev_response(s) == DEFAULT_JEV_RESPONSE


def test_settings_env_override_reads_noul_and_score(monkeypatch):
    # staging composeと同一値のenv("0.90"/"3.6")がfloatへ写像されること
    monkeypatch.setenv("LATCH_LLM_STUB_JEV_NOUL", "0.90")
    monkeypatch.setenv("LATCH_LLM_STUB_JEV_SCORE", "3.6")
    s = Settings()
    assert s.llm_stub_jev_noul == 0.9
    assert s.llm_stub_jev_score == 3.6


def test_settings_env_override_full_clean(monkeypatch):
    # LLM_ENV_VARSに新env名が入っていること( delenv網羅)の別経路確認
    for var in LLM_ENV_VARS:
        monkeypatch.delenv(var, raising=False)
    monkeypatch.setenv("LATCH_LLM_STUB_JEV_NOUL", "0.7")
    s = Settings()
    assert s.llm_stub_jev_noul == 0.7
    assert s.llm_stub_jev_score == 2.0  # 未設定なら既定


def test_build_llm_gateway_passes_settings_values_to_stub(monkeypatch):
    s = _clean_settings(monkeypatch, llm_stub_jev_noul=0.9, llm_stub_jev_score=3.6)
    gw = build_llm_gateway(FakeClock(datetime(2026, 10, 3, 12, 0, 0, tzinfo=UTC)), s)
    answers = gw._jev._jev_response["answers"]
    for key in NOUL_KEYS:
        assert answers[key]["noul"] == 0.9, key
    for key in SCORE_KEYS:
        assert answers[key]["score"] == 3.6, key
    # 判定値以外はstub定数のまま(設計§2.2 — confidence・model・usageは固定)
    assert answers["purpose_fit"]["confidence"] == 0.5
    assert gw._jev._jev_response["model"] == "jev-1.13.0"
    assert gw._jev._jev_response["usage"] == {
        "input_tokens": 0,
        "output_tokens": 0,
    }


def test_build_worker_gateway_passes_settings_values_to_stub(monkeypatch):
    s = _clean_settings(monkeypatch, llm_stub_jev_noul=0.9, llm_stub_jev_score=3.6)
    gw = build_worker_gateway(FakeClock(datetime(2026, 10, 3, 12, 0, 0, tzinfo=UTC)), s)
    answers = gw._jev._jev_response["answers"]
    for key in NOUL_KEYS:
        assert answers[key]["noul"] == 0.9, key
    for key in SCORE_KEYS:
        assert answers[key]["score"] == 3.6, key
    assert gw._jev is gw._jev_fallback  # stub時は同一インスタンス(design §2.8)
