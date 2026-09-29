"""Jev共通部品(System One)。質問定数・正規化テキスト・出力検証(M2 ws-5)。

07 §4の実装化。外部SDKに依存しない純部品(定数・純関数・dataclassのみ)で、
TypeSafe第一候補(llm/typesafe.py)・フォールバックLLM(llm/anthropic_jev.py)・
JevWorker(worker/jev.py)の3者が共有する(§9-1)。
出力検証は防御的(07 §4): 失敗は再試行せず縮退(JevOutputInvalidError)。
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime

from latch.core.clock import JST
from latch.llm.errors import JevOutputInvalidError

JEV_MODEL = "jev-1.13.0"  # 07 §4・エイリアス不使用

JEV_QUESTIONS: dict = {
    "would_a_accept_b": {
        "type": "noul",
        "instructions": {
            "a": "`state.intent_a`",
            "b": "`state.intent_b`",
            "question": "`a`の作成者が`b`との成立を提示されたときyesと答える確率。"
            "`a`の[hard]条件を満たすか、`a`の[soft]条件と判定不能NG条件に"
            "触れないことを含めて判定する。`b`が`a`の[hard]条件と意味的に"
            "矛盾する場合(予算感の著しい乖離が食事内容を成立させない等)は"
            "yesから遠ざける",
        },
        "criteria": {
            "true": "成立し得る提案である",
            "false": "成立しない提案である",
        },
    },
    "would_b_accept_a": {
        "type": "noul",
        "instructions": {
            "a": "`state.intent_b`",
            "b": "`state.intent_a`",
            "question": "`a`の作成者が`b`との成立を提示されたときyesと答える確率。"
            "`a`の[hard]条件を満たすか、`a`の[soft]条件と判定不能NG条件に"
            "触れないことを含めて判定する。`b`が`a`の[hard]条件と意味的に"
            "矛盾する場合(予算感の著しい乖離が食事内容を成立させない等)は"
            "yesから遠ざける",
        },
        "criteria": {
            "true": "成立し得る提案である",
            "false": "成立しない提案である",
        },
    },
    "purpose_fit": {
        "type": "score",
        "instructions": "`a`と`b`の目的・カテゴリの適合度(飲みたい×食べたいのズレ等)",
        "criteria": [
            "目的が根本的に異なる",
            "目的が近いが核心がずれる",
            "目的が部分的に重なる",
            "ほぼ同一目的",
            "同一目的",
        ],
    },
    "mood_fit": {
        "type": "score",
        "instructions": "`a`と`b`の雰囲気・軽さの適合度(「軽く」×「がっつり」等)",
        "criteria": [
            "雰囲気が相容れない",
            "重さが大きく異なる",
            "どちらでも成立する",
            "重さが近い",
            "雰囲気が同一",
        ],
    },
    "timing_fit": {
        "type": "score",
        "instructions": "`a`と`b`の時間帯・所要時間の適合度",
        "criteria": [
            "時間帯が合わない",
            "開始は合うが所要が合わない",
            "開始・所要が部分的に重なる",
            "時間帯・所要が近い",
            "同一時間帯",
        ],
    },
    "social_fit": {
        "type": "score",
        "instructions": "`a`と`b`の人数・社会的文脈(立場・関係性)の適合度",
        "criteria": [
            "人数・文脈が成立しない",
            "人数は合うが社会的文脈がずれる",
            "人数・文脈が部分的に合う",
            "人数・文脈が近い",
            "人数・文脈が同一",
        ],
    },
    "latent_yes": {
        "type": "noul",
        "instructions": "どちらかが明示していないが、そのIntentの記述の範囲内で"
        "YESになり得る可能性(「焼肉に行きたい」×「今日は肉系ならどこでもいい」等)",
    },
}

NOUL_KEYS = ("would_a_accept_b", "would_b_accept_a", "latent_yes")
SCORE_KEYS = ("purpose_fit", "mood_fit", "timing_fit", "social_fit")
JEV_RESULT_KEYS = frozenset((*NOUL_KEYS, *SCORE_KEYS))
SCORE_MAX_LEVEL = 4  # 5段階の最大レベル(design §2.4・supervisor承認の解釈記録)

_INVALID = "jev answers invalid"


@dataclass(frozen=True)
class JevTextInput:
    """Jev正規化テキスト導出の入力。raw_textを保持しない(構造的ピン)。"""

    category_primary: str
    structured_data: dict
    participants_min: int | None
    participants_max: int | None
    time_start: datetime | None
    time_end: datetime | None
    budget_max: int | None
    geo_radius_m: int | None


def _jst_hm(value: datetime) -> str:
    return value.astimezone(JST).strftime("%Y-%m-%d %H:%M")


def _jst_hm_only(value: datetime) -> str:
    """time行の終了側はHH:MM(design §2.3表「YYYY-MM-DD HH:MM–HH:MM」)。"""
    return value.astimezone(JST).strftime("%H:%M")


def build_jev_text(inp: JevTextInput, *, label: str) -> str:
    """Jev正規化テキスト(07 §4・§9-3の行生成規則。上から固定順)。

    含めないもの: visibility・notification_level・alcohol_involved・
    category_secondary・raw_text。省略が起きる行は飛ばし、空行を挿入しない。
    """
    lines: list[str] = [f"{label}:"]
    lines.append(f"[hard] category: {inp.category_primary}")
    if inp.time_start is not None:
        # time_endは補完後の値(JevWorker側でdefault_time_end適用済み)。
        # 終了側はHH:MMのみ(design §2.3表・07 §4例「20:00–23:00」)
        lines.append(
            f"[hard] time: {_jst_hm(inp.time_start)}–{_jst_hm_only(inp.time_end)}"
        )
    location_name = inp.structured_data.get("location_name")
    if location_name:
        radius = inp.geo_radius_m
        if radius is not None and radius % 1000 == 0:
            radius_text = f"半径{radius // 1000}km"
        else:
            # Noneのときも1000mへ補完しない(そのままm表記・起きない前提の防御)
            radius_text = f"半径{radius}m"
        lines.append(f"[hard] location: {location_name} {radius_text}")
    if inp.participants_min is not None and inp.participants_max is not None:
        if inp.participants_min == inp.participants_max:
            lines.append(f"[hard] participants: {inp.participants_min}人")
        else:
            lines.append(
                f"[hard] participants: {inp.participants_min}–{inp.participants_max}人"
            )
    if inp.budget_max is not None:
        lines.append(f"[hard] budget_max: {inp.budget_max}円")
    soft = inp.structured_data.get("soft_constraints") or []
    for item in soft:
        text = item["text"]
        if item.get("downgraded_from_ng") is True:
            text = f"{text}(システムで判定不能)"
        lines.append(f"[soft] {text}")
    return "\n".join(lines)


def _field(answer: object, key: str) -> object:
    """answerの値取り出し(dict/オブジェクト両対応 — §9-4)。"""
    if isinstance(answer, dict):
        return answer.get(key)
    return getattr(answer, key, None)


def _as_number(value: object, low: float, high: float) -> float:
    """bool除外の実数で値域検査しfloatへ(§9-4)。違反はJevOutputInvalidError。"""
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise JevOutputInvalidError(_INVALID)
    result = float(value)
    if not (low <= result <= high):
        raise JevOutputInvalidError(_INVALID)
    return result


def _as_confidence(value: object) -> float | None:
    """confidence: None または 0.0〜1.0の実数。"""
    if value is None:
        return None
    return _as_number(value, 0.0, 1.0)


def validate_and_normalize(envelope: object) -> dict:
    """System One envelopeの検証・正規化(07 §4・design §2.4)。

    入力は `{"model": ..., "answers": {7キー: answer}, "usage": ...}`。
    model/usageは検証対象外。scoreは最大レベル4で割って[0,1]へ正規化
    (丸めなし)。provider/modelはresult部に含めない(GatewayがJevJudgmentへ
    載せる)。違反は再試行せずJevOutputInvalidError(実装不整合)。
    """
    if not isinstance(envelope, dict):
        raise JevOutputInvalidError(_INVALID)
    answers = envelope.get("answers")
    if not isinstance(answers, dict) or set(answers) != set(JEV_RESULT_KEYS):
        raise JevOutputInvalidError(_INVALID)
    noul_values = {
        key: _as_number(_field(answers[key], "noul"), 0.0, 1.0) for key in NOUL_KEYS
    }
    axis: dict = {}
    for key in SCORE_KEYS:
        answer = answers[key]
        raw_score = _as_number(_field(answer, "score"), 0.0, 4.0)
        axis[key] = {
            "value": raw_score / SCORE_MAX_LEVEL,
            "confidence": _as_confidence(_field(answer, "confidence")),
        }
    axis["latent_yes"] = {"value": noul_values["latent_yes"], "confidence": None}
    return {
        "would_a_accept_b": noul_values["would_a_accept_b"],
        "would_b_accept_a": noul_values["would_b_accept_a"],
        "jev_5axis": axis,
    }


@dataclass(frozen=True)
class JevJudgment:
    """judge_pairの戻り値(design §2.2)。Layer 4は jev_result = {**result,
    "provider": judgment.provider, "model": judgment.model} を書き込む。"""

    provider: str  # "typesafe_jev" | "fallback_llm"
    model: str | None  # 第一候補時の応答バージョンID・フォールバック時はNone
    result: dict  # validate_and_normalize の出力(jev_resultのresult部)


__all__ = [
    "JEV_MODEL",
    "JEV_QUESTIONS",
    "JEV_RESULT_KEYS",
    "NOUL_KEYS",
    "SCORE_KEYS",
    "JevJudgment",
    "JevTextInput",
    "build_jev_text",
    "validate_and_normalize",
]
