"""Calibration記録の純計算部(M3 ws-1 design §2.5・§2.6・§2.11・§2.12)。

prediction組み立て(1対1評価行3段階特定・グループminペア選択)と
segment判定(09 §2.3)。DB・asyncを持たない(unit試験は決定的)。
"""

from __future__ import annotations

import json

from latch.worker.matching.layer3 import bigrams


def segment_texts(structured_data: object) -> tuple[str, ...]:
    """segment判定対象のテキスト組(09 §2.3・design §2.6)。

    category_secondary(保存形式の平キー・latch_engine._read_intent_inputsと
    同一の読み方)が非nullならその1要素、nullならsoft_constraints全文言
    (downgraded_from_ngを含む — 09 §2.3は文言からの除外を指定していない)。
    soft_texts(語彙重なり・降格除外)とは対象が違う別関数(Layer 3は06 §4の
    確定値・segmentは09 §2.3の文言指定)。構造想定外は空(安全側)。
    """
    if isinstance(structured_data, str):
        try:
            structured_data = json.loads(structured_data)
        except json.JSONDecodeError:
            return ()
    if not isinstance(structured_data, dict):
        return ()
    secondary = structured_data.get("category_secondary")
    if isinstance(secondary, str) and secondary:
        return (secondary,)
    raw = structured_data.get("soft_constraints")
    if not isinstance(raw, list):
        return ()
    return tuple(
        item["text"]
        for item in raw
        if isinstance(item, dict)
        and isinstance(item.get("text"), str)
        and bool(item["text"])
    )


def classify_segment(texts_a: tuple[str, ...], texts_b: tuple[str, ...]) -> str:
    """bigram積集合が非空→lexical・空→semantic(双方空はsemantic)。"""
    if bigrams(texts_a) & bigrams(texts_b):
        return "lexical"
    return "semantic"
