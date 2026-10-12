"""A/B割付・ペア適用の純関数群(05 v0.8 第2節「割付と適用の実装規定」・09 §5)。

DB・asyncを持たない純計算のみ(unit試験は決定的・CF側と共有)。
ハッシュは決定的(SHA-256)で、並行する初回適用が同じ値を計算する。
永続化後の真実はab_assignments行(再計算しない)。
"""

from __future__ import annotations

import hashlib
import uuid
from collections.abc import Iterable

VARIANT_CONTROL = "control"
VARIANT_TREATMENT = "treatment"
VARIANTS = (VARIANT_CONTROL, VARIANT_TREATMENT)

# 層 = 割付時点のActive Intent数(05 v0.8 実装規定2・3)。3層は分析上の分割で
# あり、層境界の変更はdocs改版を経る(割付済み行のstratumは書き換えない)
STRATA = ("0", "1-2", "3+")

# サーバ側適用点の実験キーと既定値(対照値=現行運用値。05 v0.8 実装規定5:
# 実験行が存在しない環境の挙動は従来と一字変わらない)
KEY_LATCH_THRESHOLD = "latch_threshold"
KEY_NOTIFICATION_DAILY = "notification_daily_limit"
KEY_NOTIFICATION_CONCURRENT = "notification_concurrent_limit"


def stratum_of(active_intent_count: int) -> str:
    """Active Intent数→層ラベル('0' / '1-2' / '3+')。"""
    if active_intent_count <= 0:
        return "0"
    if active_intent_count <= 2:
        return "1-2"
    return "3+"


def assign_variant(experiment_key: str, stratum: str, user_id: uuid.UUID) -> str:
    """層化ハッシュ割付(05 v0.8 実装規定3)。

    sha256("{experiment_key}:{stratum}:{user_id}")の先頭バイトが奇数なら
    treatment。決定的+層内でおおむね50/50。
    """
    digest = hashlib.sha256(f"{experiment_key}:{stratum}:{user_id}".encode()).digest()
    return VARIANT_TREATMENT if digest[0] % 2 == 1 else VARIANT_CONTROL


def pair_variant(variants: Iterable[str]) -> str:
    """ペア/集合の適用群(05 v0.8 実装規定4・FR-25)。

    参加者全員がtreatmentのときのみtreatment。混在・空はcontrol
    (保守側=対照値。時間交互切替しない)。
    """
    values = set(variants)
    if values == {VARIANT_TREATMENT}:
        return VARIANT_TREATMENT
    return VARIANT_CONTROL


def value_for(variant: str, control_value: str, treatment_value: str) -> str:
    """群→適用値のtext(型変換は呼び出し側の適用点が担う)。"""
    if variant == VARIANT_TREATMENT:
        return treatment_value
    return control_value


def parse_float(raw: str, default: float) -> float:
    """適用値のfloat解析。不正な実験行の値は対照値(既定)へフォールバック。"""
    try:
        return float(raw)
    except (TypeError, ValueError):
        return default


def parse_int(raw: str, default: int) -> int:
    """適用値のint解析。不正な実験行の値は対照値(既定)へフォールバック。"""
    try:
        return int(raw)
    except (TypeError, ValueError):
        return default
