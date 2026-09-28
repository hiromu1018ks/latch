"""g1gate/report.pyのunit(design §4-6)。レポートYAMLの構造とsha256。"""

from datetime import datetime

import yaml

from latch.core.clock import JST
from latch.g1gate.cases import AlcoholCase, ErrorCase, ParserStructCase
from latch.g1gate.compare import (
    compare_alcohol_case,
    compare_struct_case,
    error_outcome,
    summarize_alcohol,
    summarize_struct,
)
from latch.g1gate.report import build_report, sha256_file, sha256_text, write_report
from latch.intents.schema import ParserOutput

EXECUTED_AT = datetime(2026, 9, 28, 12, 34, 56, tzinfo=JST)


def _output(**overrides) -> ParserOutput:
    base = {
        "category": {"primary": "drinking", "secondary": None},
        "alcohol_involved": True,
        "time": {"start": "2026-10-01T20:00:00+09:00"},
        "location": {"name": "天文館"},
        "budget": {"max": 3000},
        "participants": {"min": None, "max": None},
    }
    base.update(overrides)
    return ParserOutput.model_validate(base)


def _struct_case() -> ParserStructCase:
    return ParserStructCase.model_validate(
        {
            "id": "P-901",
            "text": "t",
            "author_age": 26,
            "expected": {
                "category": "drinking",
                "alcohol_involved": True,
                "time": {"start": "2026-10-01T20:00:00+09:00"},
                "location": {"name": "天文館"},
                "participants": {"min": None, "max": None},
                "budget": {"max": 3000},
            },
            "needs_review": False,
        }
    )


def _error_outcome():
    return error_outcome(
        ErrorCase.model_validate(
            {
                "id": "E-901",
                "text": "t",
                "expected": {"error": "422 VALIDATION_ERROR"},
                "needs_review": False,
            }
        ),
        actual_code="422 VALIDATION_ERROR",
    )


def _build() -> dict:
    struct_outcomes = [compare_struct_case(_struct_case(), _output(), None)]
    alcohol_outcomes = [
        compare_alcohol_case(
            AlcoholCase.model_validate(
                {
                    "id": "A-901",
                    "text": "t",
                    "expected": {"alcohol_involved": True},
                    "needs_review": False,
                }
            ),
            _output(),
            None,
        )
    ]
    error_outcomes = [_error_outcome()]
    return build_report(
        executed_at=EXECUTED_AT,
        model="claude-haiku-4-5",
        llm_mode="real",
        parser_prompt_sha256=sha256_text("prompt"),
        input_sets={
            "parser_struct": {"file": "g1-parser-struct.yaml", "sha256": "a" * 64},
            "alcohol": {"file": "g1-alcohol.yaml", "sha256": "b" * 64},
        },
        base_current_datetime="2026-10-01T11:30:00+09:00",
        limit=None,
        struct=summarize_struct(struct_outcomes),
        struct_cases=struct_outcomes,
        alcohol_reference={"tp": 1, "fp": 0, "fn": 0, "tn": 0},
        alcohol=summarize_alcohol(alcohol_outcomes),
        alcohol_cases=alcohol_outcomes,
        error_results=error_outcomes,
        incomplete=False,
    )


def _build_incomplete() -> dict:
    struct_outcomes = [compare_struct_case(_struct_case(), None, "llm_unavailable")]
    alcohol_outcomes = []
    return build_report(
        executed_at=EXECUTED_AT,
        model="claude-haiku-4-5",
        llm_mode="real",
        parser_prompt_sha256="x",
        input_sets={},
        base_current_datetime="2026-10-01T11:30:00+09:00",
        limit=None,
        struct=summarize_struct(struct_outcomes),
        struct_cases=struct_outcomes,
        alcohol_reference={},
        alcohol=summarize_alcohol(alcohol_outcomes),
        alcohol_cases=alcohol_outcomes,
        error_results=[],
        incomplete=True,
    )


def test_sha256_text_known_vector():
    # Review Focus #5: sha256が実際に内容を追跡する(既知ベクトルで検証)
    assert (
        sha256_text("abc")
        == "ba7816bf8f01cfea414140de5dae2223b00361a396177a9cb410ff61f20015ad"
    )


def test_sha256_file_matches_text(tmp_path):
    path = tmp_path / "input.yaml"
    path.write_text("abc", encoding="utf-8")
    assert sha256_file(path) == sha256_text("abc")


def test_build_report_structure():
    report = _build()
    assert report["overall_passed"] is True
    assert report["incomplete"] is False
    meta = report["meta"]
    assert meta["trial_id"] == "g1-20260928-123456"
    assert meta["executed_at"] == EXECUTED_AT.isoformat()
    assert meta["llm_mode"] == "real"
    assert meta["model"] == "claude-haiku-4-5"
    assert meta["parser_prompt_sha256"] == sha256_text("prompt")
    assert meta["input_sets"]["parser_struct"]["sha256"] == "a" * 64
    assert meta["base_current_datetime"] == "2026-10-01T11:30:00+09:00"
    assert meta["limit"] is None
    # parser_struct: 閾値とフィールド別一致率・ケース詳細(actualを含む)
    ps = report["parser_struct"]
    assert ps["fields"]["category"]["rate"] == 1.0
    assert ps["passed"] is True
    (case_detail,) = ps["cases"]
    assert case_detail["id"] == "P-901"
    assert case_detail["actual"]["location"]["name"] == "天文館"
    assert case_detail["error"] is None
    # alcohol: TP/FP/FN/TN・recall/precision
    al = report["alcohol"]
    assert (al["tp"], al["fp"], al["fn"], al["tn"]) == (1, 0, 0, 0)
    assert al["passed"] is True
    assert report["alcohol_reference"] == {"tp": 1, "fp": 0, "fn": 0, "tn": 0}
    (error_detail,) = report["error_cases"]
    assert error_detail["ok"] is True


def test_build_report_fails_when_incomplete():
    # build_reportはincompleteをoverall_passedへ反映する
    report = _build_incomplete()
    assert report["incomplete"] is True
    assert report["overall_passed"] is False


def test_write_report_roundtrip(tmp_path):
    report = _build()
    path = write_report(report, out_dir=tmp_path, executed_at=EXECUTED_AT)
    assert path.name == "g1-result-20260928-123456.yaml"
    loaded = yaml.safe_load(path.read_text(encoding="utf-8"))
    assert loaded == report  # YAML往復で同一構造
