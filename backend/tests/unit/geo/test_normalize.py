"""normalize_nameの規則(design §2.4)。入力(Parser出力)と地物名の双方に適用する。"""

import pytest

from latch.geo.normalize import normalize_name


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        # 無変換(かな・漢字・漢数字はそのまま通る)
        ("天文館一丁目", "天文館一丁目"),
        ("伊敷町", "伊敷町"),
        # 丁目の算用数字→漢数字(ISJの大字町丁目名は漢数字)
        ("天文館1丁目", "天文館一丁目"),
        ("2丁目", "二丁目"),
        ("中央10丁目", "中央十丁目"),
        ("中央21丁目", "中央二十一丁目"),
        # NFKC(全角英数→半角)
        ("Ｂａｒ", "bar"),
        ("ｱｲ", "あい"),  # NFKCで半角カナ→全角カナ→(次段で)ひらがな
        # 空白類(全角含む)・区切り記点の除去
        ("　天文館　一丁目", "天文館一丁目"),
        ("バ・ヨイマチ", "ばよいまち"),  # カタカナも次段でひらがな化される
        ("バー・ヨイマチ", "ばーよいまち"),  # 長音ー(Lm)は保持
        ("伊敷-町", "伊敷町"),
        # 「大字」接頭辞の除去(ISJの大字町丁目名)
        ("大字草牟田", "草牟田"),
        # カタカナ→ひらがな統一
        ("カフェミナミ", "かふぇみなみ"),
        ("ヴィラ", "ゔぃら"),
        # ASCII小文字化
        ("CafeMinami", "cafeminami"),
        # 組み合わせ
        ("鹿児島市 天文館1丁目", "鹿児島市天文館一丁目"),
        # 空文字・記号のみ
        ("", ""),
        ("　", ""),
        # 々(Lm)は保持
        ("代々木町", "代々木町"),
    ],
)
def test_normalize_name(raw, expected):
    assert normalize_name(raw) == expected


def test_normalize_is_idempotent():
    # 二重適用で変化しない(取り込み側・照合側の適用回数に依存しない)
    once = normalize_name("鹿児島市 天文館1丁目")
    assert normalize_name(once) == once
