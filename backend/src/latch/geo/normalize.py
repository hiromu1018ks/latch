"""地物名称の正規化(design §2.4)。照合(正転)は完全一致のみ — 入力と地物名の
双方にこの同じ関数を適用してから等値比較する。

規則(この順で適用):
1. NFKC正規化(全角英数→半角・半角カナ→全角)
2. 空白類と区切り記号類の除去(Unicodeカテゴリ Z*/P*/S* を除去。
   長音「ー」(Lm)・々(Lm)・漢数字・かな・数字(Nd)は保持)
3. 「大字」接頭辞の除去(ISJの大字町丁目名)
4. カタカナ→ひらがな統一(ァ〜ヴ。ヶ(U+30F6)は地名に常用のため対象外)
5. 「<算用数字>丁目」の算用数字→漢数字(ISJの大字町丁目名は漢数字)
6. ASCII小文字化
"""

from __future__ import annotations

import re
import unicodedata

_DIGITS = "〇一二三四五六七八九"
_CHO_PATTERN = re.compile(r"(\d+)丁目")


def _digits_to_kanji(number: str) -> str:
    """1〜99の算用数字を漢数字へ(丁目の数字用)。範囲外はそのまま。"""
    n = int(number)
    if not 1 <= n <= 99:
        return number
    tens, ones = divmod(n, 10)
    if tens == 0:
        return _DIGITS[ones]
    tens_part = "十" if tens == 1 else _DIGITS[tens] + "十"
    return tens_part + (_DIGITS[ones] if ones else "")


def _strip_symbols(text: str) -> str:
    return "".join(
        ch for ch in text if not unicodedata.category(ch).startswith(("Z", "P", "S"))
    )


def normalize_name(name: str) -> str:
    """地物名称の正規化(design §2.4)。取り込み時と照合時の双方に適用する。"""
    nfkc = unicodedata.normalize("NFKC", name)
    stripped = _strip_symbols(nfkc)
    if stripped.startswith("大字"):
        stripped = stripped[2:]
    hiragana = "".join(
        chr(ord(ch) - 0x60) if "ァ" <= ch <= "ヴ" else ch for ch in stripped
    )
    kanji = _CHO_PATTERN.sub(lambda m: _digits_to_kanji(m.group(1)) + "丁目", hiragana)
    return kanji.lower()
