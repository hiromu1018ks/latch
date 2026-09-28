"""G1ゲート入力セット(docs/testassets/)の読み込みと検証(design §3.1・§2.8)。

YAMLはconfirmed・読み取り専用(10 §2)。ここで行うのは構造検証(pydantic)と
meta整合(件数・true/false・基準日時の一致)。期待値の中身の照合はcompare.py。
"""

from __future__ import annotations

from datetime import datetime
from pathlib import Path

import yaml
from pydantic import BaseModel, ConfigDict

from latch.core.clock import JST

# docs/testassets/README.md「共通の前提」: 相対表現の解決基準(2026-10-01 11:30 JST)。
# {current_date}へ注入する値の前提 — meta.current_datetimeと一致しない入力セットは
# 期待値の前提が崩れているため読み込み段階で拒否する(design §2.8)
BASE_CURRENT_DATETIME = datetime(2026, 10, 1, 11, 30, tzinfo=JST)


class _IgnoreExtra(BaseModel):
    """余分キー(note・category_ref等)は無視(構造検証に必要なキーのみ持つ)。"""

    model_config = ConfigDict(extra="ignore")


class ExpectedTime(_IgnoreExtra):
    start: str  # ISO8601(+09:00)。照合はcompare.pyがdatetime化
    end: str | None = None


class ExpectedLocation(_IgnoreExtra):
    name: str


class ExpectedParticipants(_IgnoreExtra):
    min: int | None = None
    max: int | None = None


class ExpectedBudget(_IgnoreExtra):
    max: int | None = None


class StructExpectation(_IgnoreExtra):
    """D-17測定対象5フィールド+alcohol参考値(README「測定対象外のフィールド」)。"""

    category: str
    alcohol_involved: bool
    time: ExpectedTime
    location: ExpectedLocation
    participants: ExpectedParticipants
    budget: ExpectedBudget


class ParserStructCase(_IgnoreExtra):
    id: str
    text: str
    author_age: int | None = None
    expected: StructExpectation
    needs_review: bool


class ErrorExpectation(_IgnoreExtra):
    error: str  # "422 VALIDATION_ERROR"
    # missing等は参照情報(05ペイロード設計・design §6-6)であり照合対象外


class ErrorCase(_IgnoreExtra):
    id: str
    text: str
    author_age: int | None = None
    expected: ErrorExpectation
    needs_review: bool


class AlcoholExpectation(_IgnoreExtra):
    alcohol_involved: bool
    # category_refは判定の一助(README)で照合対象外


class AlcoholCase(_IgnoreExtra):
    id: str
    text: str
    author_age: int | None = None
    expected: AlcoholExpectation
    needs_review: bool


class _MetaBase(_IgnoreExtra):
    status: str
    created: str
    current_datetime: datetime  # pydanticがISO8601文字列をパース(tz-aware)


class ParserStructMeta(_MetaBase):
    case_count: int
    error_case_count: int


class AlcoholMeta(_MetaBase):
    case_count: int
    true_count: int
    false_count: int


class ParserStructSet(_IgnoreExtra):
    meta: ParserStructMeta
    cases: list[ParserStructCase]
    error_cases: list[ErrorCase]


class AlcoholSet(_IgnoreExtra):
    meta: AlcoholMeta
    cases: list[AlcoholCase]


def _load_yaml(path: Path) -> dict:
    with path.open(encoding="utf-8") as f:
        data = yaml.safe_load(f)
    if not isinstance(data, dict):
        raise ValueError(f"{path}: トップレベルはマッピングである必要があります")
    return data


def _verify_meta_current(meta: _MetaBase, *, kind: str) -> None:
    if meta.status != "confirmed":
        raise ValueError(f"{kind}: statusがconfirmedではありません: {meta.status!r}")
    if meta.current_datetime != BASE_CURRENT_DATETIME:
        raise ValueError(
            f"{kind}: meta.current_datetime({meta.current_datetime.isoformat()})"
            f"が基準日時({BASE_CURRENT_DATETIME.isoformat()})と一致しません"
            f" — 期待値の前提が崩れています(design §2.8)"
        )


def _verify_unique_ids(ids: list[str], *, kind: str) -> None:
    if len(set(ids)) != len(ids):
        duplicated = sorted({i for i in ids if ids.count(i) > 1})
        raise ValueError(f"{kind}: ケースidが重複しています: {duplicated}")


def load_parser_struct(path: Path) -> ParserStructSet:
    """g1-parser-struct.yamlを読み、構造・meta整合・id一意を検証する。"""
    parsed = ParserStructSet.model_validate(_load_yaml(path))
    _verify_meta_current(parsed.meta, kind=path.name)
    _verify_unique_ids(
        [c.id for c in parsed.cases] + [c.id for c in parsed.error_cases],
        kind=path.name,
    )
    if len(parsed.cases) != parsed.meta.case_count:
        raise ValueError(
            f"{path.name}: cases {len(parsed.cases)}件がmeta.case_count"
            f" {parsed.meta.case_count}と一致しません"
        )
    if len(parsed.error_cases) != parsed.meta.error_case_count:
        raise ValueError(
            f"{path.name}: error_cases {len(parsed.error_cases)}件が"
            f"meta.error_case_count {parsed.meta.error_case_count}と一致しません"
        )
    return parsed


def load_alcohol(path: Path) -> AlcoholSet:
    """g1-alcohol.yamlを読み、構造・meta整合(true/false数)・id一意を検証する。"""
    parsed = AlcoholSet.model_validate(_load_yaml(path))
    _verify_meta_current(parsed.meta, kind=path.name)
    _verify_unique_ids([c.id for c in parsed.cases], kind=path.name)
    if len(parsed.cases) != parsed.meta.case_count:
        raise ValueError(
            f"{path.name}: cases {len(parsed.cases)}件がmeta.case_count"
            f" {parsed.meta.case_count}と一致しません"
        )
    true_count = sum(1 for c in parsed.cases if c.expected.alcohol_involved)
    false_count = len(parsed.cases) - true_count
    if true_count != parsed.meta.true_count or false_count != parsed.meta.false_count:
        raise ValueError(
            f"{path.name}: true_count/false_count実測({true_count}/{false_count})が"
            f"meta({parsed.meta.true_count}/{parsed.meta.false_count})と"
            f"一致しません"
        )
    return parsed
