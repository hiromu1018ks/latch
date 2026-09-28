"""G1ゲートの照合・集計・閾値判定(design §2.9)。すべて純粋関数(実API不要)。

07 §5 D-17: category 85% / time.start 90% / location 90% / participants 80% / budget 90%
09 §4.3(FR-20): alcohol_involved recall 100%・precision下限90%
一致率の分母は入力セット件数で固定 — 出力の得られなかったケースで分母を
減らして合格に見せない(design §2.9「全件への応答が得られて初めて成立」)。
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import datetime

from latch.g1gate.cases import AlcoholCase, ErrorCase, ParserStructCase
from latch.intents.schema import ParserOutput

# 07 §5 D-17(入力セット30件以上での基準)。判定は rate >= threshold
THRESHOLDS = {
    "category": 0.85,
    "time_start": 0.90,
    "location": 0.90,
    "participants": 0.80,
    "budget": 0.90,
}
ALCOHOL_RECALL_MIN = 1.0  # 09 §4.3: 正例の見逃し0件
ALCOHOL_PRECISION_MIN = 0.90  # 09 §4.3: 偽陽性2件まで許容(36件セット)

STRUCT_FIELDS = ("category", "time_start", "location", "participants", "budget")


def normalize_text(s: str) -> str:
    """空白正規化: 空白類をすべて除去(location一致の前処理)。

    日本語地名の語間空白の揺れ(「天文 館」/「天文館」)を吸収するため、
    空白の保持ではなく除去で正規化する(09 §4.3: 表記の揺れは一致扱い)。
    """
    return re.sub(r"\s+", "", s.strip())


def category_match(expected: str, actual: str) -> bool:
    """category.primaryのLiteral等値(design §2.9)。"""
    return expected == actual


def time_start_match(expected_iso: str, actual: datetime) -> bool:
    """tz-awareの瞬間等値(+09:00/Z表記の揺れは吸収)。"""
    return datetime.fromisoformat(expected_iso) == actual


def location_match(expected: str, actual: str) -> bool:
    """地域一致(09 §4.3・確定値10)。空白正規化のうえ双方向部分一致。

    空文字列は任意文字列の部分文字列となるため、正規化後空は不一致とする
    (偽陽性防止。actual空はParserLocationのmin_length=1で通常起きない)。
    """
    e = normalize_text(expected)
    a = normalize_text(actual)
    if not e or not a:
        return False
    return e in a or a in e


def participants_match(
    expected: tuple[int | None, int | None],
    actual_min: int | None,
    actual_max: int | None,
) -> bool:
    """(min, max)組の等値(None含む)。"""
    return expected == (actual_min, actual_max)


def budget_match(expected: int | None, actual: int | None) -> bool:
    return expected == actual


@dataclass(frozen=True)
class FieldResult:
    """1フィールドの照合結果(expected/actualはJSON安全値)。"""

    expected: object
    actual: object
    match: bool


@dataclass(frozen=True)
class StructCaseOutcome:
    """struct 1ケースの結果。actualはParserOutputのdict化(生応答記録・§2.10)。"""

    id: str
    fields: dict[str, FieldResult]
    alcohol_involved: FieldResult  # 参考値(design §6-5: 合否判定外)
    actual: dict | None  # 出力なし(422/503系)はNone
    error: str | None  # "unstructurable"|"llm_unavailable"|"unexpected:<Class>"|None


def compare_struct_case(
    case: ParserStructCase,
    structured: ParserOutput | None,
    error: str | None,
) -> StructCaseOutcome:
    """1ケースの照合。structured=None(出力なし)は全フィールド不一致扱い。"""
    if structured is None:
        fields = {
            name: FieldResult(
                expected=_expected_snapshot(case, name), actual=None, match=False
            )
            for name in STRUCT_FIELDS
        }
        alcohol = FieldResult(
            expected=case.expected.alcohol_involved, actual=None, match=False
        )
        return StructCaseOutcome(
            id=case.id,
            fields=fields,
            alcohol_involved=alcohol,
            actual=None,
            error=error,
        )
    dump = structured.model_dump(mode="json")
    fields = {
        "category": FieldResult(
            case.expected.category,
            structured.category.primary,
            category_match(case.expected.category, structured.category.primary),
        ),
        "time_start": FieldResult(
            case.expected.time.start,
            dump["time"]["start"],
            time_start_match(case.expected.time.start, structured.time.start),
        ),
        "location": FieldResult(
            case.expected.location.name,
            structured.location.name,
            location_match(case.expected.location.name, structured.location.name),
        ),
        "participants": FieldResult(
            [case.expected.participants.min, case.expected.participants.max],
            dump["participants"],
            participants_match(
                (case.expected.participants.min, case.expected.participants.max),
                structured.participants.min,
                structured.participants.max,
            ),
        ),
        "budget": FieldResult(
            case.expected.budget.max,
            structured.budget.max,
            budget_match(case.expected.budget.max, structured.budget.max),
        ),
    }
    alcohol = FieldResult(
        case.expected.alcohol_involved,
        structured.alcohol_involved,
        case.expected.alcohol_involved == structured.alcohol_involved,
    )
    return StructCaseOutcome(
        id=case.id,
        fields=fields,
        alcohol_involved=alcohol,
        actual=dump,
        error=error,
    )


def _expected_snapshot(case: ParserStructCase, name: str) -> object:
    """出力なし時のexpected記録(レポート用のJSON安全値)。"""
    if name == "category":
        return case.expected.category
    if name == "time_start":
        return case.expected.time.start
    if name == "location":
        return case.expected.location.name
    if name == "participants":
        return [case.expected.participants.min, case.expected.participants.max]
    return case.expected.budget.max


@dataclass(frozen=True)
class FieldSummary:
    match: int
    total: int
    rate: float
    threshold: float
    passed: bool


@dataclass(frozen=True)
class StructSummary:
    fields: dict[str, FieldSummary]
    passed: bool


def summarize_struct(outcomes: list[StructCaseOutcome]) -> StructSummary:
    """フィールド別一致率の集計とD-17閾値判定。分母=len(outcomes)で固定。"""
    total = len(outcomes)
    fields: dict[str, FieldSummary] = {}
    for name in STRUCT_FIELDS:
        match = sum(1 for o in outcomes if o.fields[name].match)
        rate = match / total if total else 0.0
        threshold = THRESHOLDS[name]
        fields[name] = FieldSummary(
            match=match,
            total=total,
            rate=rate,
            threshold=threshold,
            passed=rate >= threshold,
        )
    return StructSummary(fields=fields, passed=all(f.passed for f in fields.values()))


def alcohol_reference_counts(outcomes: list[StructCaseOutcome]) -> dict:
    """parser-struct側alcohol期待値の参考集計(design §6-5: 判定に使わない)。"""
    return {
        "tp": sum(
            1
            for o in outcomes
            if o.alcohol_involved.expected and o.alcohol_involved.actual is True
        ),
        "fp": sum(
            1
            for o in outcomes
            if not o.alcohol_involved.expected and o.alcohol_involved.actual is True
        ),
        "fn": sum(
            1
            for o in outcomes
            if o.alcohol_involved.expected and o.alcohol_involved.actual is not True
        ),
        "tn": sum(
            1
            for o in outcomes
            if not o.alcohol_involved.expected and o.alcohol_involved.actual is False
        ),
    }


def classify_alcohol(expected: bool, actual: bool | None) -> str:
    """TP/FP/FN/TN分類。出力なしは正例=FN(見逃し)・負例=unclassified。"""
    if actual is None:
        return "fn" if expected else "unclassified"
    if expected and actual:
        return "tp"
    if expected and not actual:
        return "fn"
    if not expected and actual:
        return "fp"
    return "tn"


@dataclass(frozen=True)
class AlcoholCaseOutcome:
    id: str
    expected: bool
    classification: str  # tp/fp/fn/tn/unclassified
    actual: bool | None
    actual_raw: dict | None
    error: str | None


def compare_alcohol_case(
    case: AlcoholCase, structured: ParserOutput | None, error: str | None
) -> AlcoholCaseOutcome:
    expected = case.expected.alcohol_involved
    if structured is None:
        classification = classify_alcohol(expected, None)
        return AlcoholCaseOutcome(
            id=case.id,
            expected=expected,
            classification=classification,
            actual=None,
            actual_raw=None,
            error=error,
        )
    actual = structured.alcohol_involved
    return AlcoholCaseOutcome(
        id=case.id,
        expected=expected,
        classification=classify_alcohol(expected, actual),
        actual=actual,
        actual_raw=structured.model_dump(mode="json"),
        error=error,
    )


@dataclass(frozen=True)
class AlcoholSummary:
    tp: int
    fp: int
    fn: int
    tn: int
    unclassified: int
    recall: float
    precision: float
    passed: bool


def summarize_alcohol(outcomes: list[AlcoholCaseOutcome]) -> AlcoholSummary:
    def count(kind: str) -> int:
        return sum(1 for o in outcomes if o.classification == kind)

    tp, fp, fn, tn = count("tp"), count("fp"), count("fn"), count("tn")
    unclassified = count("unclassified")
    recall = tp / (tp + fn) if (tp + fn) else 1.0
    precision = tp / (tp + fp) if (tp + fp) else 1.0
    passed = (
        recall >= ALCOHOL_RECALL_MIN
        and precision >= ALCOHOL_PRECISION_MIN
        and unclassified == 0
    )
    return AlcoholSummary(
        tp=tp,
        fp=fp,
        fn=fn,
        tn=tn,
        unclassified=unclassified,
        recall=recall,
        precision=precision,
        passed=passed,
    )


@dataclass(frozen=True)
class ErrorCaseOutcome:
    id: str
    expected_error: str
    actual: str
    ok: bool


def error_outcome(case: ErrorCase, actual_code: str) -> ErrorCaseOutcome:
    """エラー付帯ケースは422 VALIDATION_ERRORが返ることのみ確認(§2.9)。"""
    return ErrorCaseOutcome(
        id=case.id,
        expected_error=case.expected.error,
        actual=actual_code,
        ok=actual_code == "422 VALIDATION_ERROR",
    )
