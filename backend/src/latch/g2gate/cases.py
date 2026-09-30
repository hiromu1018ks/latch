"""G2評価ゴールドセット(docs/testassets/g2-jev-goldset.yaml)の読み込みと検証。

YAMLはconfirmed・読み取り専用(g1gate casesと同一規律)。構造検証(pydantic)
+meta整合(件数・gold構成・基準日時)+id一意・ペア参照存在をfail-fastで行う。
SHA256をGoldsetへ載せて返す(レポートmetaの証跡)。
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

import yaml
from pydantic import BaseModel, ConfigDict

from latch.core.clock import JST
from latch.llm.jev import JevTextInput

# goldset meta.current_datetimeと一致検査する基準日時(README「共通の前提」
# と同一・g1と同時刻)。正規化テキストの組み立てはClock非依存だが、Gatewayの
# 送信記録occurred_atがこの基準になる
G2_BASE_CURRENT_DATETIME = datetime(2026, 10, 1, 11, 30, tzinfo=JST)


class _IgnoreExtra(BaseModel):
    model_config = ConfigDict(extra="ignore")


class StructuredCategory(_IgnoreExtra):
    primary: str
    secondary: str | None = None


class StructuredTime(_IgnoreExtra):
    start: str  # ISO8601(+09:00)。アプリ層補完後のためendは必須(機械検査済み)
    end: str


class StructuredLocation(_IgnoreExtra):
    name: str
    radius_m: int  # 補完後(Null→1000)のため必須


class StructuredBudget(_IgnoreExtra):
    max: int | None = None


class StructuredParticipants(_IgnoreExtra):
    min: int
    max: int


class StructuredIntent(_IgnoreExtra):
    category: StructuredCategory
    alcohol_involved: bool
    time: StructuredTime
    location: StructuredLocation
    budget: StructuredBudget
    participants: StructuredParticipants
    soft_constraints: list[str] = []
    ng_unverifiable: list[str] = []


class GoldsetIntent(_IgnoreExtra):
    id: str
    user: str
    author_age: int | None = None
    structured: StructuredIntent
    source: str | None = None


class BandExpectation(_IgnoreExtra):
    label: str | None = None  # latent_yesはbandのみ
    band: str


class PairExpected(_IgnoreExtra):
    gold_mutual: bool
    would_a_accept_b: BandExpectation
    would_b_accept_a: BandExpectation
    purpose_fit: int
    mood_fit: int
    timing_fit: int
    social_fit: int
    latent_yes: BandExpectation


class GoldsetPair(_IgnoreExtra):
    id: str
    intent_a: str
    intent_b: str
    kind: str
    segment: str
    layer1_pass: bool
    expected: PairExpected
    flag: bool = False
    fail_reason: str | None = None
    source: str | None = None


class GoldsetMeta(_IgnoreExtra):
    status: str
    created: str
    current_datetime: datetime
    pair_count: int
    intent_count: int
    gold_true: int
    gold_false: int


class _GoldsetRoot(_IgnoreExtra):
    meta: GoldsetMeta
    intents: list[GoldsetIntent]
    pairs: list[GoldsetPair]


@dataclass(frozen=True)
class Goldset:
    pairs: list[GoldsetPair]
    inputs: dict[str, JevTextInput]
    expected: dict[str, PairExpected]
    yaml_sha256: str
    meta: GoldsetMeta


def _load_yaml(path: Path) -> dict:
    with path.open(encoding="utf-8") as f:
        data = yaml.safe_load(f)
    if not isinstance(data, dict):
        raise ValueError(f"{path}: トップレベルはマッピングである必要があります")
    return data


def _verify_unique_ids(ids: list[str], *, kind: str) -> None:
    if len(set(ids)) != len(ids):
        duplicated = sorted({i for i in ids if ids.count(i) > 1})
        raise ValueError(f"{kind}: idが重複しています: {duplicated}")


def _to_jev_input(item: GoldsetIntent) -> JevTextInput:
    """intents→JevTextInput(goldset-plan §11手順3・07 §4正規化テキストの材料)。

    ng_unverifiableはdowngraded_from_ng=Trueのsoft行へ変換(引用#12)。
    順序は{Falseの通常行}+{ng_unverifiable由来行}。visibility・
    notification_levelはJevTextInput自体が持たないため構造的に含まれない。
    """
    s = item.structured
    soft: list[dict] = [
        {"text": t, "downgraded_from_ng": False} for t in s.soft_constraints
    ]
    soft += [{"text": t, "downgraded_from_ng": True} for t in s.ng_unverifiable]
    return JevTextInput(
        category_primary=s.category.primary,
        structured_data={
            "location_name": s.location.name,
            "soft_constraints": soft,
        },
        participants_min=s.participants.min,
        participants_max=s.participants.max,
        time_start=datetime.fromisoformat(s.time.start),
        time_end=datetime.fromisoformat(s.time.end),
        budget_max=s.budget.max,
        geo_radius_m=s.location.radius_m,
    )


def load_goldset(path: Path) -> Goldset:
    """g2-jev-goldset.yamlを読み、構造・meta整合を検証してGoldsetを返す。"""
    parsed = _GoldsetRoot.model_validate(_load_yaml(path))
    if parsed.meta.status != "confirmed":
        raise ValueError(
            f"{path.name}: statusがconfirmedではありません: {parsed.meta.status!r}"
        )
    if parsed.meta.current_datetime != G2_BASE_CURRENT_DATETIME:
        raise ValueError(
            f"{path.name}: meta.current_datetime"
            f"({parsed.meta.current_datetime.isoformat()})が基準日時"
            f"({G2_BASE_CURRENT_DATETIME.isoformat()})と一致しません"
        )
    _verify_unique_ids([i.id for i in parsed.intents], kind=path.name)
    _verify_unique_ids([p.id for p in parsed.pairs], kind=path.name)
    if len(parsed.pairs) != parsed.meta.pair_count:
        raise ValueError(
            f"{path.name}: pairs {len(parsed.pairs)}件がmeta.pair_count"
            f" {parsed.meta.pair_count}と一致しません"
        )
    if len(parsed.intents) != parsed.meta.intent_count:
        raise ValueError(
            f"{path.name}: intents {len(parsed.intents)}件がmeta.intent_count"
            f" {parsed.meta.intent_count}と一致しません"
        )
    gold_true = sum(1 for p in parsed.pairs if p.expected.gold_mutual)
    gold_false = len(parsed.pairs) - gold_true
    if gold_true != parsed.meta.gold_true or gold_false != parsed.meta.gold_false:
        raise ValueError(
            f"{path.name}: gold構成(true {gold_true}/false {gold_false})が"
            f"meta(gold_true {parsed.meta.gold_true}/"
            f"gold_false {parsed.meta.gold_false})と一致しません"
        )
    intent_ids = {i.id for i in parsed.intents}
    for p in parsed.pairs:
        for ref in (p.intent_a, p.intent_b):
            if ref not in intent_ids:
                raise ValueError(f"{path.name}: ペア{p.id}が未知のintentを参照: {ref}")
        if p.intent_a == p.intent_b:
            raise ValueError(f"{path.name}: ペア{p.id}が自己ペア: {p.intent_a}")
    return Goldset(
        pairs=parsed.pairs,
        inputs={i.id: _to_jev_input(i) for i in parsed.intents},
        expected={p.id: p.expected for p in parsed.pairs},
        yaml_sha256=hashlib.sha256(path.read_bytes()).hexdigest(),
        meta=parsed.meta,
    )
