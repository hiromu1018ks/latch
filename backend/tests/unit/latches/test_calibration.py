"""calibration純計算部のunit試験(M3 ws-1 design §4.1)。

segment判定・評価行3段階特定・minペア選択・prediction組み立て。
すべて純関数(決定的・DBなし)。
"""

from latch.latches.calibration import (
    classify_segment,
    segment_texts,
)


def test_segment_texts_uses_secondary_when_present():
    """category_secondary非nullならその1要素(引用#14・design §2.6)。"""
    sd = {
        "category_secondary": "焼肉",
        "soft_constraints": [{"text": "静かな店で"}],
    }
    assert segment_texts(sd) == ("焼肉",)


def test_segment_texts_falls_back_to_soft_constraints_including_downgraded():
    """secondaryがnullならsoft_constraints全文言(降格文言を含む — 09 §2.3)。"""
    sd = {
        "category_secondary": None,
        "soft_constraints": [
            {"text": "静かな店で"},
            {"text": "深夜でも", "downgraded_from_ng": True},
            {"downgraded_from_ng": True},  # textなし要素は除外
            "不正要素",
        ],
    }
    assert segment_texts(sd) == ("静かな店で", "深夜でも")


def test_segment_texts_both_missing_returns_empty():
    """双方null・soft_constraintsなしは空(→semanticへ流れる)。"""
    assert segment_texts({"category_secondary": None}) == ()
    assert segment_texts("not-a-dict") == ()  # 構造想定外は安全側
    assert segment_texts('{"category_secondary": "焼肉"}') == ("焼肉",)  # str JSONB


def test_classify_segment_lexical_on_shared_bigram():
    """表層トークン一致(bigram積集合非空)→lexical(引用#14)。"""
    assert classify_segment(("焼肉",), ("焼肉",)) == "lexical"
    assert classify_segment(("焼肉食べたい",), ("焼肉に行こう",)) == "lexical"


def test_classify_segment_semantic_on_no_overlap():
    """一致なし→semantic。双方空もsemantic(design §2.6)。"""
    assert classify_segment(("焼肉",), ("イタリアン",)) == "semantic"
    assert classify_segment((), ()) == "semantic"
