"""Calibration記録の純計算部(M3 ws-1 design §2.5・§2.6・§2.11・§2.12)。

prediction組み立て(1対1評価行3段階特定・グループminペア選択)と
segment判定(09 §2.3)。DB・asyncを持たない(unit試験は決定的)。
"""

from __future__ import annotations

import json
import uuid
from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal

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


@dataclass(frozen=True)
class EvalRow:
    """1対1の評価行材料(jev_resultあり行・design §2.11)。

    rowsは呼び出し側SQLが updated_at DESC で並べたもの(第1段の「一致行の
    最新」=走査最初の一致で表現できる)。
    """

    jev_result: dict
    latch_score: Decimal | None
    updated_at: datetime


def pick_eval_row(
    rows: list[EvalRow], score: Decimal, latch_created: datetime
) -> EvalRow | None:
    """1対1の評価行3段階特定(design §2.11)。

    第1段=latch_score一致の最新・第2段=updated_at<=latches.created_atの最新・
    第3段=最新(世代不問)。全段失敗=None(呼び出し側はレコードを作らず
    構造化ログのみ — design §2.5)。NUMERIC同値判定(Decimal ==・丸め問題なし)。
    """
    for row in rows:  # 第1段
        if row.latch_score == score:
            return row
    for row in rows:  # 第2段
        if row.updated_at <= latch_created:
            return row
    return rows[0] if rows else None  # 第3段


@dataclass(frozen=True)
class PairEvalRow:
    """グループの1ペア評価行(UNIQUE(a,b,va,vb)で一意・design §2.12)。"""

    a: uuid.UUID
    b: uuid.UUID
    va: int
    vb: int
    jev_result: dict


def select_versioned_pairs(
    rows: list[PairEvalRow], versions: dict[uuid.UUID, int]
) -> list[PairEvalRow]:
    """versions一致の全ペア評価行(members昇順の組合せごとに高々1行)。"""
    out: list[PairEvalRow] = []
    members = sorted(versions)
    for i in range(len(members)):
        for j in range(i + 1, len(members)):
            a, b = members[i], members[j]
            for r in rows:
                if (
                    r.a == a
                    and r.b == b
                    and r.va == versions[a]
                    and r.vb == versions[b]
                ):
                    out.append(r)
                    break
    return out


def pick_min_pair(rows: list[PairEvalRow]) -> PairEvalRow | None:
    """MutualScore最小ペア(同点は(a,b)辞書順最小で決定的・design §2.12)。"""
    if not rows:
        return None

    def key(r: PairEvalRow):
        return (
            min(
                float(r.jev_result["would_a_accept_b"]),
                float(r.jev_result["would_b_accept_a"]),
            ),
            r.a,
            r.b,
        )

    return min(rows, key=key)


def build_prediction(jev_result: dict, l_score: float, segment: str) -> dict:
    """prediction dict(07 §6・引用#13・#14)。jev_resultから必要キーを写す。

    MutualScore=min(would_a, would_b)。Lはlatches.score(提示時・引用#26)。
    """
    return {
        "would_a_accept_b": float(jev_result["would_a_accept_b"]),
        "would_b_accept_a": float(jev_result["would_b_accept_a"]),
        "MutualScore": min(
            float(jev_result["would_a_accept_b"]),
            float(jev_result["would_b_accept_a"]),
        ),
        "L": l_score,
        "jev_5axis": jev_result["jev_5axis"],
        "provider": jev_result["provider"],
        "model": jev_result["model"],
        "segment": segment,
    }
