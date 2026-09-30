"""g2gate部品(cases・compare・report・__main__)のunit試験(ws-8 design §2.8・§4.1)。

合成入力のみ(実APIなし・g1gateと同一規律)。指標は手計算既知値で検証する。
"""

import hashlib
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

import pytest
import yaml

from latch.core.clock import JST
from latch.g2gate.cases import (
    G2_BASE_CURRENT_DATETIME,
    load_goldset,
)
from latch.g2gate.compare import (
    THRESHOLDS,
    auc_separation,
    band_of,
    brier_score,
    compute_metrics,
    expected_calibration_error,
)
from latch.g2gate.report import build_report, write_report
from latch.llm.jev import JevJudgment

BASE = {
    "meta": {
        "status": "confirmed",
        "created": "2026-09-29",
        "current_datetime": "2026-10-01T11:30:00+09:00",
        "pair_count": 1,
        "intent_count": 2,
        "gold_true": 1,
        "gold_false": 0,
    },
    "intents": [
        {
            "id": "SI-001",
            "user": "U-001",
            "author_age": 34,
            "structured": {
                "category": {"primary": "drinking", "secondary": "居酒屋"},
                "alcohol_involved": True,
                "time": {
                    "start": "2026-10-01T20:00:00+09:00",
                    "end": "2026-10-01T23:00:00+09:00",
                },
                "location": {"name": "天文館", "radius_m": 1000},
                "budget": {"max": 3500},
                "participants": {"min": 2, "max": 2},
                "soft_constraints": ["軽く飲みたい"],
                "ng_unverifiable": ["会社関係の人は避けたい"],
            },
            "source": "drafted",
        },
        {
            "id": "SI-002",
            "user": "U-002",
            "author_age": 30,
            "structured": {
                "category": {"primary": "drinking"},
                "alcohol_involved": True,
                "time": {
                    "start": "2026-10-01T20:00:00+09:00",
                    "end": "2026-10-01T23:00:00+09:00",
                },
                "location": {"name": "天文館", "radius_m": 1000},
                "budget": {"max": 3000},
                "participants": {"min": 2, "max": 2},
                "soft_constraints": [],
                "ng_unverifiable": [],
            },
            "source": "machine",
        },
    ],
    "pairs": [
        {
            "id": "GP-001",
            "intent_a": "SI-001",
            "intent_b": "SI-002",
            "kind": "1to1",
            "segment": "lexical",
            "layer1_pass": True,
            "expected": {
                "gold_mutual": True,
                "would_a_accept_b": {"label": "accept", "band": "high"},
                "would_b_accept_a": {"label": "accept", "band": "high"},
                "purpose_fit": 4,
                "mood_fit": 3,
                "timing_fit": 4,
                "social_fit": 4,
                "latent_yes": {"band": "mid"},
            },
            "rationale": "test",
            "flag": False,
            "source": "drafted",
        }
    ],
}


def _write_goldset(tmp_path: Path, data: dict | None = None) -> Path:
    path = tmp_path / "g2-jev-goldset.yaml"
    path.write_text(
        yaml.safe_dump(data if data is not None else BASE, allow_unicode=True),
        encoding="utf-8",
    )
    return path


def test_load_goldset_returns_inputs_and_sha(tmp_path):
    g = load_goldset(_write_goldset(tmp_path))
    assert g.meta.status == "confirmed"
    assert (
        g.yaml_sha256
        == hashlib.sha256((tmp_path / "g2-jev-goldset.yaml").read_bytes()).hexdigest()
    )
    a = g.inputs["SI-001"]
    assert a.category_primary == "drinking"
    assert a.time_start == datetime(2026, 10, 1, 20, 0, tzinfo=JST)
    assert a.budget_max == 3500
    assert a.geo_radius_m == 1000
    assert g.expected["GP-001"].gold_mutual is True


def test_load_goldset_ng_unverifiable_becomes_downgraded_soft(tmp_path):
    g = load_goldset(_write_goldset(tmp_path))
    soft = g.inputs["SI-001"].structured_data["soft_constraints"]
    # 順序: {Falseの通常行} + {ng_unverifiable由来行}(引用#12・07 §4)
    assert soft == [
        {"text": "軽く飲みたい", "downgraded_from_ng": False},
        {"text": "会社関係の人は避けたい", "downgraded_from_ng": True},
    ]


def test_load_goldset_rejects_unconfirmed(tmp_path):
    data = yaml.safe_load(yaml.safe_dump(BASE, allow_unicode=True))
    data["meta"]["status"] = "draft"
    with pytest.raises(ValueError, match="confirmed"):
        load_goldset(_write_goldset(tmp_path, data))


def test_load_goldset_rejects_wrong_current_datetime(tmp_path):
    data = yaml.safe_load(yaml.safe_dump(BASE, allow_unicode=True))
    data["meta"]["current_datetime"] = "2026-10-02T11:30:00+09:00"
    with pytest.raises(ValueError, match="current_datetime"):
        load_goldset(_write_goldset(tmp_path, data))


def test_load_goldset_rejects_count_mismatch(tmp_path):
    data = yaml.safe_load(yaml.safe_dump(BASE, allow_unicode=True))
    data["meta"]["pair_count"] = 2
    with pytest.raises(ValueError, match="pair_count"):
        load_goldset(_write_goldset(tmp_path, data))


def test_load_goldset_rejects_gold_true_mismatch(tmp_path):
    data = yaml.safe_load(yaml.safe_dump(BASE, allow_unicode=True))
    data["meta"]["gold_true"] = 0
    with pytest.raises(ValueError, match="gold"):
        load_goldset(_write_goldset(tmp_path, data))


def test_load_goldset_rejects_unknown_pair_reference(tmp_path):
    data = yaml.safe_load(yaml.safe_dump(BASE, allow_unicode=True))
    data["pairs"][0]["intent_b"] = "SI-999"
    with pytest.raises(ValueError, match="SI-999"):
        load_goldset(_write_goldset(tmp_path, data))


def test_base_current_datetime_pin():
    assert G2_BASE_CURRENT_DATETIME == datetime(2026, 10, 1, 11, 30, tzinfo=JST)
    assert G2_BASE_CURRENT_DATETIME == datetime.fromisoformat(
        "2026-10-01T11:30:00+09:00"
    )


# -- compare(手計算既知値・design §4.1) --


@dataclass(frozen=True)
class _Outcome:  # runner.PairOutcomeと同じ形(duck-typing)
    pair_id: str
    route: str
    judgment: JevJudgment | None
    error: str | None

    @property
    def mutual_score(self) -> float | None:
        if self.judgment is None:
            return None
        r = self.judgment.result
        return min(r["would_a_accept_b"], r["would_b_accept_a"])


def _judgment(would_a: float, would_b: float) -> JevJudgment:
    result = {
        "would_a_accept_b": would_a,
        "would_b_accept_a": would_b,
        "jev_5axis": {
            k: {"value": 0.5, "confidence": None}
            for k in ("purpose_fit", "mood_fit", "timing_fit", "social_fit")
        }
        | {"latent_yes": {"value": 0.5, "confidence": None}},
    }
    return JevJudgment(provider="typesafe_jev", model="jev-1.13.0", result=result)


def _goldset_with_gold(gold: dict[str, bool]):
    """compute_metrics試験用の最小goldset(expectedのみ使用)。"""
    from latch.g2gate.cases import Goldset, PairExpected

    expected = {}
    for pair_id, g in gold.items():
        exp = PairExpected(
            gold_mutual=g,
            would_a_accept_b={"label": "accept", "band": "high"},
            would_b_accept_a={"label": "accept", "band": "high"},
            purpose_fit=4,
            mood_fit=4,
            timing_fit=4,
            social_fit=4,
            latent_yes={"label": None, "band": "mid"},
        )
        expected[pair_id] = exp
    return Goldset(pairs=[], inputs={}, expected=expected, yaml_sha256="x", meta=None)


def test_thresholds_pin():
    assert THRESHOLDS == (0.70, 0.80, 0.90)  # 09 §4.2・D-01


def test_band_of_boundaries():
    """band境界: low<0.35 / 0.35≤mid<0.65 / high≥0.65(goldset-plan §5)。"""
    assert band_of(0.349) == "low"
    assert band_of(0.35) == "mid"
    assert band_of(0.649) == "mid"
    assert band_of(0.65) == "high"
    assert band_of(1.0) == "high"


def test_precision_recall_known_values():
    """P/R手計算: L=[0.9(T),0.85(T),0.4(F)]・閾値0.80→提案2件・TP2・FP0。"""
    m = compute_metrics(
        [
            _Outcome("p1", "first", _judgment(0.95, 0.9), None),
            _Outcome("p2", "first", _judgment(0.9, 0.85), None),
            _Outcome("p3", "first", _judgment(0.5, 0.4), None),
        ],
        _goldset_with_gold({"p1": True, "p2": True, "p3": False}),
    )
    t80 = m["thresholds"]["0.8"]
    assert t80["precision"] == 1.0
    assert t80["recall"] == 1.0
    t70 = m["thresholds"]["0.7"]
    assert t70["precision"] == 1.0
    t90 = m["thresholds"]["0.9"]
    # 閾値0.90: 提案={0.95, 0.9}(0.9は0.90>=0.90で提案)→ p1,p2 が提案
    assert t90["precision"] == 1.0


def test_ece_known_value():
    """ECE手計算: pred 0.9(gold T)と0.1(gold F)→ 各bin |0.9-1.0|=0.1・|0.1-0.0|=0.1
    → 加重平均 0.5*(0.1+0.1)=0.1。"""
    ece = expected_calibration_error([0.9, 0.1], [True, False])
    assert ece == pytest.approx(0.1)


def test_brier_known_value():
    """Brier手計算: (0.9-1)^2+(0.1-0)^2=0.02 → 平均0.01。"""
    assert brier_score([0.9, 0.1], [True, False]) == pytest.approx(0.01)


def test_auc_perfect_reversal_and_tie():
    """分離度: 完全分離=1.0・完全逆転=0.0・完全タイ=0.5(Mann-Whitney U)。"""
    assert auc_separation([0.9, 0.8], [0.3, 0.2]) == 1.0
    assert auc_separation([0.2, 0.3], [0.8, 0.9]) == 0.0
    assert auc_separation([0.5, 0.5], [0.5, 0.5]) == 0.5
    # 部分タイ: pos=[0.9,0.5] neg=[0.5,0.1] → 勝3(0.9>0.5,0.9>0.1,0.5>0.1)
    # +タイ0.5(0.5=0.5) → U=(3+0.5)/4=0.875
    assert auc_separation([0.9, 0.5], [0.5, 0.1]) == pytest.approx(0.875)


def test_compute_metrics_counts_failures_and_diagnostics():
    """失敗ペアは指標分母から除外せず失敗数で報告(design §2.8)。"""
    outcomes = [
        _Outcome("p1", "first", _judgment(0.95, 0.9), None),
        _Outcome("p2", "first", None, "LLMRateLimitError"),
    ]
    m = compute_metrics(outcomes, _goldset_with_gold({"p1": True, "p2": True}))
    assert m["pair_count"] == 2
    assert m["success_count"] == 1
    assert m["failure_count"] == 1
    assert m["failures"] == {"LLMRateLimitError": 1}
    # diagnostics: fit軸の±1一致率(期待4・実測0.5*4=2.0 → |2-4|=2>1 → 不一致)
    assert m["diagnostics"]["fit_within_1_rate"]["purpose_fit"] == 0.0


# -- report/__main__/runner(design §4.1・§2.8) --


def test_build_report_partial_flag_and_note():
    """--limit時はpartial=true・レポート先頭に「部分実行=証拠外」(design §2.8)。"""
    report = build_report(
        executed_at=datetime(2026, 10, 1, 11, 30, tzinfo=JST),
        route="both",
        goldset_file="g2-jev-goldset.yaml",
        goldset_sha256="sha",
        questions_sha="qsha",
        metrics_by_route={"first": {}, "fallback": {}},
        outcomes=[],
        usage_totals={"first": {"input_tokens": 10}, "fallback": {}},
        limit=2,
    )
    first_key = next(iter(report))
    assert first_key == "note"
    assert "部分実行" in report["note"]
    assert report["meta"]["partial"] is True
    assert report["meta"]["pair_count"] == 0


def test_build_report_full_run_has_no_note():
    report = build_report(
        executed_at=datetime(2026, 10, 1, 11, 30, tzinfo=JST),
        route="first",
        goldset_file="g2-jev-goldset.yaml",
        goldset_sha256="sha",
        questions_sha="qsha",
        metrics_by_route={"first": {}},
        outcomes=[],
        usage_totals={"first": {}},
        limit=None,
    )
    assert "note" not in report
    assert report["meta"]["partial"] is False


def test_write_report_basename(tmp_path):
    executed = datetime(2026, 10, 1, 20, 30, tzinfo=JST)
    path = write_report(
        {"meta": {"partial": True}}, out_dir=tmp_path, executed_at=executed
    )
    assert path.name == "g2-jev-result-20261001-203000.yaml"
    assert path.exists()


def test_main_rejects_stub_mode(monkeypatch, capsys):
    """起動検証: stubのまま実測したと錯覚させない(g1gateと同一規律)。"""
    monkeypatch.setenv("LATCH_LLM_MODE", "stub")
    from latch.g2gate.__main__ import main

    assert main([]) == 2
    assert "real" in capsys.readouterr().err


def test_main_rejects_missing_typesafe_key(monkeypatch, capsys):
    monkeypatch.setenv("LATCH_LLM_MODE", "real")
    monkeypatch.setenv("LATCH_ANTHROPIC_API_KEY", "x")
    monkeypatch.setenv("LATCH_GEMINI_API_KEY", "x")
    monkeypatch.delenv("LATCH_TYPESAFE_API_KEY", raising=False)
    from latch.g2gate.__main__ import main

    assert main([]) == 2
    assert "TYPESAFE" in capsys.readouterr().err


def test_run_routes_uses_public_if_and_counts_failures(monkeypatch, tmp_path):
    """run_routesはcall_jev_first/fallback直呼び・失敗は記録して継続。"""
    import asyncio

    from latch.g2gate.runner import run_routes
    from latch.llm.errors import LLMRateLimitError

    class _Gateway:
        def __init__(self):
            self.first_calls = 0
            self.fallback_calls = 0

        async def call_jev_first(self, *, intent_a, intent_b, intent_ids):
            self.first_calls += 1
            if self.first_calls == 1:
                raise LLMRateLimitError("429")
            return _judgment(0.9, 0.9)

        async def call_jev_fallback(self, *, intent_a, intent_b, intent_ids):
            self.fallback_calls += 1
            return _judgment(0.8, 0.8)

    goldset = load_goldset(_write_goldset(tmp_path))
    outcomes = asyncio.run(run_routes(_Gateway(), goldset, route="both"))
    assert len(outcomes) == 2  # 合成goldsetは1ペア×両経路
    assert [o.route for o in outcomes] == ["first", "fallback"]
    assert outcomes[0].error == "LLMRateLimitError"
    assert outcomes[0].mutual_score is None
    assert outcomes[1].mutual_score == 0.8
