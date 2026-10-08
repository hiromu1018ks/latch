"""domain契約型(T2 Interfaces: IntentSnapshot・CommandResult・EvaluationKey・
ClaimResult・ScheduleRequest)。

DB非依存のdataclass/enumのみ。Workers版(D1)とPostgreSQL版の両方が
これらを共有する。表現規律は計画§3.1: UUIDは文字列・時刻は呼び出し側で
domain.time の変換を使う(API入出力は既存ISO 8601のまま)。
"""

from __future__ import annotations

import enum
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any


class CommandStatus(enum.StrEnum):
    """apply_* の結果状態(計画§3.2)。"""

    APPLIED = "applied"
    CONFLICT = "conflict"  # 楽観的排他的な版・世代の不一致
    REJECTED = "rejected"  # 業務条件不成立(409/422相当)


@dataclass(frozen=True)
class CommandResult:
    """業務commandの実行結果。

    response はAPI応答として返す保存済みdict(機微情報を含まない
    直列化済み形式)。revision は対象の確定後の版。
    """

    status: CommandStatus
    response: dict[str, Any] | None = None
    revision: int | None = None
    error_code: str | None = None  # REJECTED/CONFLICT時の既存error code
    # このcommandで確定した副作用参照(outbox・通知job・journalのid)
    effects: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class IntentSnapshot:
    """検索・評価の入力となるIntentの不変snapshot(読取時点)。

    Workers版ではD1読取結果から構成し、Python計算(latch_calc等)へ
    渡す。SQLAlchemyのRow型を持たない。
    """

    intent_id: str
    version: int
    user_id: str
    status: str
    category_primary: str
    category_secondary: str | None
    alcohol_involved: bool
    time_start: datetime
    time_end: datetime | None
    flexibility_minutes: int | None
    geo_lon: float | None
    geo_lat: float | None
    radius_m: int | None
    budget_max: int | None
    participants_min: int | None
    participants_max: int | None
    soft_constraints: tuple[str, ...] = ()
    negative_constraints: tuple[str, ...] = ()
    ng_unverifiable: tuple[str, ...] = ()
    visibility: str = "hidden_until_match"
    notification_level: str = "all"
    expires_at: datetime | None = None


@dataclass(frozen=True)
class EvaluationKey:
    """同一評価の処理権確保(I1)・評価cache(I2)の厳密キー。

    pair_key は正規順序(a<b)のペアID。fingerprint は実際に送る入力と
    判定規則の版を含む(§5.3)。Intentのversionは適用先確認に使い、
    キーの一意性には含めない。
    """

    kind: str  # "jev" | "embedding" | "parse"
    pair_key: str  # "jev:{a}:{b}:{a_ver}:{b_ver}" 等・正規順序
    fingerprint: str  # 入力+normalizer+prompt+schema+provider/model+policy版
    context_version: str = ""  # 使った時刻文脈・外部事実の版


@dataclass(frozen=True)
class ClaimResult:
    """claim_evaluation の結果(I1・§4)。"""

    acquired: bool
    fencing_token: int | None = None
    lease_expires_at: datetime | None = None
    stale_owner: bool = False  # lease切れの旧処理を検出した場合


@dataclass(frozen=True)
class ScheduleRequest:
    """再評価要求(I3・§5.4)。"""

    intent_id: str
    requested_revision: int
    reason: str  # "initial" | "updated" | "timer" | "catchup" | "retry" | ...
    due_at: datetime
