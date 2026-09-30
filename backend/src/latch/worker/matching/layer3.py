"""Layer 3 Cheap Judge(06 §4・design §2.2・§2.3)。

cheap_score = 0.5×類似度 + 0.3×ルールスコア + 0.2×語彙重なり の線形合成。
すべて決定的(依存ゼロ・追加APIコストゼロ)。06 §4 は要素と重みのみを確定し
正規化の詳細を規定しないため、実装定義は design §2.2(重みと同じく運用
データで調整対象)。語彙抽出に形態素解析器を使わない(依存追加と辞書版依存
の非決定性 — design §2.2)。D-04降格(downgraded_from_ng=true)は語彙重なり
の計算対象から明示的に除外しLayer 4入力へ渡る(06 §4)。丸めない(同点判定と
決定性を桁依存にしない — design §2.2)。
"""

from __future__ import annotations

import json
import unicodedata
import uuid
from dataclasses import dataclass
from datetime import datetime

# 06 §8 D-24: Cheap Judge から Layer 4 へ渡す出力数の上限
K_CHEAP = 20
# design §2.2 実装定義の初期値(調整はこの定数1箇所に閉じる — design §5-8)
TIME_SATURATION_MIN = 180  # Intent実効寿命3時間(03 D-19)を飽和点に
BUDGET_SATURATION_YEN = 3000  # Layer 1のペア予算fail線500円(06 §2)の6倍
NEUTRAL = 0.5  # NULL・空語彙の中立値(満点にも零点にもしない)
# 06 §4 の重み(初期値・運用データで調整 — 引用#1)
WEIGHT_SIMILARITY = 0.5
WEIGHT_RULE = 0.3
WEIGHT_VOCAB = 0.2


@dataclass(frozen=True)
class ScoredCandidate:
    """Layer 3 通過候補(Layer 2 結果+cheap_score。design §2.3)。"""

    intent_id: uuid.UUID
    version: int
    similarity: float
    cheap_score: float


def soft_texts(structured_data: object) -> tuple[str, ...]:
    """soft_constraints から語彙抽出(降格除外 — 06 §4・引用#13)。

    jsonb列はstrで返りうるためjson.loadsで解釈する(worker/embedding.pyと
    同規律 — design §5-4)。構造が想定外の場合は空(安全側)。
    """
    if isinstance(structured_data, str):
        try:
            structured_data = json.loads(structured_data)
        except json.JSONDecodeError:
            return ()
    if not isinstance(structured_data, dict):
        return ()
    raw = structured_data.get("soft_constraints")
    if not isinstance(raw, list):
        return ()
    return tuple(
        item["text"]
        for item in raw
        if isinstance(item, dict)
        and isinstance(item.get("text"), str)
        and bool(item["text"])
        and item.get("downgraded_from_ng") is not True
    )


def time_closeness(start_a: datetime, start_b: datetime) -> float:
    """時間近さ: 1 − min(Δmin, 180)/180(design §2.2)。

    開始時刻差のみ(交差判定はLayer 1が持つ。time_endとの組合せ評価はしない)。
    """
    delta_min = abs((start_a - start_b).total_seconds()) / 60.0
    return 1.0 - min(delta_min, TIME_SATURATION_MIN) / TIME_SATURATION_MIN


def budget_closeness(budget_a: int | None, budget_b: int | None) -> float:
    """予算近さ: 1 − min(|Δ|, 3000)/3000(design §2.2)。

    NULL=制約なし(05 §2)のため近さが定義できない→中立0.5。
    """
    if budget_a is None or budget_b is None:
        return NEUTRAL
    delta = abs(budget_a - budget_b)
    return 1.0 - min(delta, BUDGET_SATURATION_YEN) / BUDGET_SATURATION_YEN


def rule_score(
    *,
    origin_time_start: datetime,
    cand_time_start: datetime,
    origin_budget: int | None,
    cand_budget: int | None,
) -> float:
    """ルールスコア: (時間近さ + 予算近さ) / 2(design §2.2の等分)。"""
    return (
        time_closeness(origin_time_start, cand_time_start)
        + budget_closeness(origin_budget, cand_budget)
    ) / 2.0


def _bigrams(texts: tuple[str, ...]) -> frozenset[str]:
    """文字bigram集合(NFKC正規化・2文字未満は空・text間をまたがない)。"""
    grams: set[str] = set()
    for text in texts:
        normalized = unicodedata.normalize("NFKC", text)
        if len(normalized) < 2:
            continue
        grams.update(normalized[i : i + 2] for i in range(len(normalized) - 1))
    return frozenset(grams)


def bigrams(texts: tuple[str, ...]) -> frozenset[str]:
    """文字bigram集合のpublic版(M3 ws-1 design §2.6)。

    segment判定(calibration)が語彙計算と同一のトークン化を使うための公開IF。
    _bigramsと同一実装(パイプライン内でトークン化を一つに保つ)。
    """
    return _bigrams(texts)


def vocab_overlap(texts_a: tuple[str, ...], texts_b: tuple[str, ...]) -> float:
    """語彙重なり: bigram集合のJaccard係数(design §2.2)。

    双方の語彙集合が空(soft constraintsなし)は中立0.5。
    """
    a = _bigrams(texts_a)
    b = _bigrams(texts_b)
    union = a | b
    if not union:
        return NEUTRAL
    return len(a & b) / len(union)


def cheap_score(similarity: float, rule: float, vocab: float) -> float:
    """0.5/0.3/0.2 の線形合成(06 §4)。丸めない。"""
    return WEIGHT_SIMILARITY * similarity + WEIGHT_RULE * rule + WEIGHT_VOCAB * vocab


def select_top_kc(scored: list[ScoredCandidate]) -> list[ScoredCandidate]:
    """上位 K_c=20(cheap_score降順・同点はintent_id昇順 — D-24・design §2.3)。"""
    return sorted(scored, key=lambda s: (-s.cheap_score, s.intent_id))[:K_CHEAP]
