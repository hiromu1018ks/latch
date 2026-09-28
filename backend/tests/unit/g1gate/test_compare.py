"""g1gate/compare.pyのunit(design §4-4・§2.9)。照合・集計・閾値の全ロジック。
すべて純粋関数のため実API不要。"""

from datetime import datetime

from latch.core.clock import JST
from latch.g1gate.cases import AlcoholCase, ErrorCase, ParserStructCase
from latch.g1gate.compare import (
    ALCOHOL_PRECISION_MIN,
    ALCOHOL_RECALL_MIN,
    THRESHOLDS,
    alcohol_reference_counts,
    budget_match,
    category_match,
    classify_alcohol,
    compare_alcohol_case,
    compare_struct_case,
    error_outcome,
    location_match,
    participants_match,
    summarize_alcohol,
    summarize_struct,
    time_start_match,
)
from latch.intents.schema import ParserOutput


def _dt(*args) -> datetime:
    return datetime(*args, tzinfo=JST)


def _output(**overrides) -> ParserOutput:
    """YAML期待値P-901(struct-minimal)に一致するParserOutput。"""
    base = {
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
    base.update(overrides)
    return ParserOutput.model_validate(base)


def _struct_case(**overrides) -> ParserStructCase:
    data = {
        "id": "P-901",
        "text": "今夜20時から天文館で飲みたい。ひとり3000円までで",
        "author_age": 26,
        "expected": {
            "category": "drinking",
            "alcohol_involved": True,
            "time": {"start": "2026-10-01T20:00:00+09:00", "end": None},
            "location": {"name": "天文館"},
            "participants": {"min": None, "max": None},
            "budget": {"max": 3000},
        },
        "needs_review": False,
    }
    for key, value in overrides.items():
        if key == "expected":
            data["expected"].update(value)
        else:
            data[key] = value
    return ParserStructCase.model_validate(data)


def _alcohol_case(expected: bool, case_id: str = "A-901") -> AlcoholCase:
    return AlcoholCase.model_validate(
        {
            "id": case_id,
            "text": "テスト",
            "author_age": 30,
            "expected": {"alcohol_involved": expected},
            "needs_review": False,
        }
    )


# --- フィールド照合(design §2.9) ---


def test_category_match_is_equality():
    assert category_match("drinking", "drinking")
    assert not category_match("meal", "drinking")


def test_time_start_match_is_instant_equality():
    assert time_start_match("2026-10-01T20:00:00+09:00", _dt(2026, 10, 1, 20, 0))
    # Z表記と+09:00表記の揺れは同一瞬間として一致(09 §4.3: 表記は問わない)
    assert time_start_match("2026-10-01T11:00:00Z", _dt(2026, 10, 1, 20, 0))
    assert not time_start_match("2026-10-01T20:00:00+09:00", _dt(2026, 10, 1, 20, 30))


def test_location_match_is_bidirectional_substring():
    # 確定値10: 地域の一致で判定(座標・表記の厳密一致は求めない)
    assert location_match("天文館", "天文館界隈")
    assert location_match("中央駅前", "鹿児島中央駅前")  # expected ⊂ actual
    assert location_match("鹿児島中央駅前", "中央駅前")  # actual ⊂ expected
    assert not location_match("天文館", "呉服町")


def test_location_match_normalizes_whitespace():
    assert location_match("天文 館", "天文館")
    assert location_match(" 天文館あたり ", "天文館あたり")


def test_location_match_empty_never_matches():
    # Review Focus #4: 空文字列は任意文字列の部分文字列になる — 正規化後空は不一致
    assert not location_match("", "天文館")
    assert not location_match("天文館", "")
    assert not location_match("", "")


def test_participants_match_is_pair_equality():
    assert participants_match((None, None), None, None)
    assert participants_match((2, 2), 2, 2)
    assert participants_match((2, 4), 2, 4)
    assert not participants_match((2, None), 2, 2)
    assert not participants_match((2, 4), 2, 2)


def test_budget_match_is_equality():
    assert budget_match(None, None)
    assert budget_match(3000, 3000)
    assert not budget_match(None, 3000)
    assert not budget_match(3000, 4000)


# --- structケース照合と集計 ---


def test_compare_struct_case_all_match():
    outcome = compare_struct_case(_struct_case(), _output(), error=None)
    assert outcome.error is None
    assert all(f.match for f in outcome.fields.values())
    assert set(outcome.fields) == {
        "category",
        "time_start",
        "location",
        "participants",
        "budget",
    }
    assert outcome.actual["category"]["primary"] == "drinking"
    assert outcome.actual["negative_constraints"] == []


def test_compare_struct_case_partial_mismatch():
    outcome = compare_struct_case(
        _struct_case(expected={"budget": {"max": 9999}}), _output(), error=None
    )
    assert outcome.fields["budget"].match is False
    assert outcome.fields["category"].match is True


def test_compare_struct_case_error_counts_all_mismatch():
    # Review Focus #2: 出力なし(422/503系)は全フィールド不一致。分母は減らさない
    outcome = compare_struct_case(_struct_case(), None, error="unstructurable")
    assert outcome.actual is None
    assert outcome.error == "unstructurable"
    assert all(not f.match for f in outcome.fields.values())


def test_summarize_struct_threshold_boundary():
    # 閾値は「>=」で判定(07 D-17)。17/20=0.85: category(0.85)は境界ちょうどで
    # 合格・time_start(0.90)は同率でも不合格 — 全フィールドが17/20になる合成で検証
    outcomes = []
    for i in range(20):
        case = _struct_case(id=f"P-{i:03d}")
        structured = _output() if i < 17 else None
        outcome = compare_struct_case(
            case, structured, error=None if i < 17 else "unstructurable"
        )
        outcomes.append(outcome)
    summary = summarize_struct(outcomes)
    assert summary.fields["category"].match == 17
    assert summary.fields["category"].total == 20
    assert summary.fields["category"].rate == 0.85
    assert summary.fields["category"].passed is True
    # time_start(0.90)は17/20=0.85のため不合格
    assert summary.fields["time_start"].passed is False
    assert summary.passed is False


def test_summarize_struct_passes_when_all_fields_meet():
    outcomes = [
        compare_struct_case(_struct_case(id=f"P-{i:03d}"), _output(), None)
        for i in range(5)
    ]
    summary = summarize_struct(outcomes)
    assert all(f.rate == 1.0 for f in summary.fields.values())
    assert summary.passed is True


def test_summarize_struct_total_is_fixed_at_input_size():
    # 分母は入力セット件数から減らない(design §2.9: 全件への応答が前提)
    outcomes = [
        compare_struct_case(_struct_case(id=f"P-{i:03d}"), None, "llm_unavailable")
        for i in range(3)
    ]
    summary = summarize_struct(outcomes)
    assert summary.fields["category"].total == 3


# --- alcohol照合と集計 ---


def test_classify_alcohol_four_cells():
    assert classify_alcohol(True, True) == "tp"
    assert classify_alcohol(True, False) == "fn"
    assert classify_alcohol(False, True) == "fp"
    assert classify_alcohol(False, False) == "tn"


def test_classify_alcohol_missing_output():
    # 出力なし: 正例はFN(見逃し)。負例は判定不能(unclassified)
    assert classify_alcohol(True, None) == "fn"
    assert classify_alcohol(False, None) == "unclassified"


def test_compare_alcohol_case_records_actual():
    ok = compare_alcohol_case(_alcohol_case(True), _output(), error=None)
    assert ok.classification == "tp"
    assert ok.actual is True
    failed = compare_alcohol_case(_alcohol_case(True), None, error="llm_unavailable")
    assert failed.classification == "fn"
    assert failed.actual is None


def test_summarize_alcohol_passes_at_precision_floor():
    # 09 §4.3: recall 100%・precision下限90%。tp=18/fp=2 → precision=0.90で合格
    outcomes = (
        [
            compare_alcohol_case(_alcohol_case(True, f"A-{i}"), _output(), None)
            for i in range(18)
        ]
        + [
            compare_alcohol_case(
                _alcohol_case(False, f"B-{i}"), _output(alcohol_involved=True), None
            )
            for i in range(2)
        ]
        + [
            compare_alcohol_case(
                _alcohol_case(False, f"C-{i}"), _output(alcohol_involved=False), None
            )
            for i in range(12)
        ]
    )
    summary = summarize_alcohol(outcomes)
    assert (summary.tp, summary.fp, summary.fn, summary.tn) == (18, 2, 0, 12)
    assert summary.recall == 1.0
    assert summary.precision == 0.9
    assert summary.passed is True


def test_summarize_alcohol_fails_on_any_miss():
    outcomes = (
        [
            compare_alcohol_case(_alcohol_case(True, f"A-{i}"), _output(), None)
            for i in range(23)
        ]
        + [
            compare_alcohol_case(
                _alcohol_case(True, "A-x"), _output(alcohol_involved=False), None
            )
        ]
        + [
            compare_alcohol_case(
                _alcohol_case(False, f"C-{i}"), _output(alcohol_involved=False), None
            )
            for i in range(12)
        ]
    )
    summary = summarize_alcohol(outcomes)
    assert summary.fn == 1
    assert summary.recall < ALCOHOL_RECALL_MIN
    assert summary.passed is False


def test_summarize_alcohol_fails_on_unclassified():
    # 出力なしの負例が1件でもあれば不合格(全ケースへの応答が前提)
    outcomes = [compare_alcohol_case(_alcohol_case(True, "A-1"), _output(), None)] + [
        compare_alcohol_case(_alcohol_case(False, "B-1"), None, error="unstructurable")
    ]
    summary = summarize_alcohol(outcomes)
    assert summary.unclassified == 1
    assert summary.passed is False


def test_alcohol_reference_counts_are_not_a_verdict():
    # design §6-5: parser-struct側のalcohol期待値は参考値(合否判定に使わない)
    outcomes = [
        compare_struct_case(
            _struct_case(id=f"P-{i:03d}"), _output(alcohol_involved=False), None
        )
        for i in range(3)
    ]
    reference = alcohol_reference_counts(outcomes)
    assert reference == {"tp": 0, "fp": 0, "fn": 3, "tn": 0}


# --- error_cases判定 ---


def test_error_outcome_ok_on_422():
    case = ErrorCase.model_validate(
        {
            "id": "E-901",
            "text": "今日誰かと飲みたい",
            "author_age": None,
            "expected": {"error": "422 VALIDATION_ERROR", "missing": ["time.start"]},
            "needs_review": False,
        }
    )
    ok = error_outcome(case, actual_code="422 VALIDATION_ERROR")
    assert ok.ok is True
    ng = error_outcome(case, actual_code="503 LLM_UNAVAILABLE")
    assert ng.ok is False


def test_thresholds_match_d17():
    assert THRESHOLDS == {
        "category": 0.85,
        "time_start": 0.90,
        "location": 0.90,
        "participants": 0.80,
        "budget": 0.90,
    }
    assert ALCOHOL_RECALL_MIN == 1.0
    assert ALCOHOL_PRECISION_MIN == 0.90
