"""正規化テキスト導出(07 §3・design §2.4)。

07 §4の規律(算術・日付処理はコード側で行う)に準じて決定的な純関数で
導出する。帳区分表はdocsに規定がなく実装定義(07 §3の例示「平日夜20-23時」
との整合のみが拘束)。入力dataclassはraw_textを保持しない — 構造的に
raw_textがEmbedding経路に入らないことのピン(確定値#10・08 §3)。
形式に含めないもの: raw_text・visibility・notification_level・budget・
alcohol_involved・category_secondary(07 §4と同じ位置づけ: 表示・通知の
制御であり判定材料でない)。
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime

from latch.core.clock import JST


@dataclass(frozen=True)
class EmbeddingTextInput:
    """正規化テキスト導出の入力(intents列の部分・raw_textなし — 確定値#10)。"""

    category_primary: str
    structured_data: dict
    participants_min: int | None
    participants_max: int | None
    time_start: datetime | None
    time_end: datetime | None


def _band(hour: int) -> str:
    """開始時刻→帯(design §2.4の固定表・全文はunit試験がピン)。"""
    if 5 <= hour < 11:
        return "朝"
    if 11 <= hour < 16:
        return "昼"
    if 16 <= hour < 19:
        return "夕方"
    if 19 <= hour < 23:
        return "夜"
    return "深夜"  # 23時・0〜4時


def _time_phrase(start: datetime | None, end: datetime | None) -> str:
    """時間帯の表現。time_start NULLは要素全体を省略(空文字を返す)。"""
    if start is None:
        return ""
    s = start.astimezone(JST)
    dow = "平日" if s.weekday() < 5 else "週末"  # 月〜金/土日
    if end is None:
        clock = f"{s.hour}時以降"
    else:
        clock = f"{s.hour}-{end.astimezone(JST).hour}時"
    return f"{dow}{_band(s.hour)}{clock}"


def _headcount_phrase(pmin: int | None, pmax: int | None) -> str:
    if pmin is None or pmax is None:
        return ""  # DB列はNOT NULL(補完済み)。片方のみの指定は起きない想定の防御
    if pmin == pmax:
        return f"{pmin}人"
    return f"{pmin}-{pmax}人"


def _soft_constraints_phrase(raw: object) -> str:
    """structured_data.soft_constraints[].textを「・」で連結(降格込み・06 §3)。"""
    if not isinstance(raw, list):
        return ""
    texts = [
        item["text"]
        for item in raw
        if isinstance(item, dict) and isinstance(item.get("text"), str) and item["text"]
    ]
    return "・".join(texts)


def build_embedding_text(inp: EmbeddingTextInput) -> str:
    """空でない要素を「 / 」で連結(空要素で区切りが残らない — 07 §3)。"""
    parts: list[str] = []
    if inp.category_primary:
        parts.append(inp.category_primary)
    time_part = _time_phrase(inp.time_start, inp.time_end)
    if time_part:
        parts.append(time_part)
    location = inp.structured_data.get("location_name")
    if isinstance(location, str) and location:
        parts.append(location)
    headcount = _headcount_phrase(inp.participants_min, inp.participants_max)
    if headcount:
        parts.append(headcount)
    soft = _soft_constraints_phrase(inp.structured_data.get("soft_constraints"))
    if soft:
        parts.append(soft)
    return " / ".join(parts)
