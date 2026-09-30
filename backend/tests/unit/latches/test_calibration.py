"""calibration純計算部のunit試験(M3 ws-1 design §4.1)。

segment判定・評価行3段階特定・minペア選択・prediction組み立て。
すべて純関数(決定的・DBなし)。
"""

import uuid
from datetime import UTC, datetime, timedelta
from decimal import Decimal

from latch.latches.calibration import (
    EvalRow,
    PairEvalRow,
    build_prediction,
    classify_segment,
    pick_eval_row,
    pick_min_pair,
    segment_texts,
    select_versioned_pairs,
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


T0 = datetime(2026, 9, 30, 12, 0, 0, tzinfo=UTC)


def _jev(wa: float, wb: float, provider: str = "typesafe_jev") -> dict:
    return {
        "would_a_accept_b": wa,
        "would_b_accept_a": wb,
        "jev_5axis": {
            "purpose_fit": {"value": 0.5, "confidence": 0.9},
            "latent_yes": {"value": 0.4, "confidence": None},
        },
        "provider": provider,
        "model": "jev-1.13.0",
    }


def test_pick_eval_row_first_stage_score_match():
    """第1段: latch_score一致行の最新(design §2.11)。rowsはupdated_at降順。"""
    r_new = EvalRow(
        jev_result=_jev(0.9, 0.8),
        latch_score=Decimal("0.85"),
        updated_at=T0 + timedelta(minutes=10),
    )
    r_old_match = EvalRow(
        jev_result=_jev(0.7, 0.7),
        latch_score=Decimal("0.70"),
        updated_at=T0,
    )
    rows = [r_new, r_old_match]  # 降順
    hit = pick_eval_row(rows, Decimal("0.70"), T0 + timedelta(hours=1))
    assert hit is r_old_match


def test_pick_eval_row_second_stage_created_at_bound():
    """第2段: score不一致なら latches.created_at 以前の最新(jev_resultあり)。"""
    newer = EvalRow(
        jev_result=_jev(0.9, 0.9),
        latch_score=None,
        updated_at=T0 + timedelta(hours=2),  # created_at後
    )
    older = EvalRow(
        jev_result=_jev(0.8, 0.8),
        latch_score=None,
        updated_at=T0 + timedelta(hours=1),  # created_at以前
    )
    rows = [newer, older]
    latch_created = T0 + timedelta(hours=1, minutes=30)
    assert pick_eval_row(rows, Decimal("0.85"), latch_created) is older


def test_pick_eval_row_third_stage_falls_back_to_latest():
    """第3段: 第2段でも取れないなら同対の最新(世代不問)。全行なしはNone。"""
    only = EvalRow(
        jev_result=_jev(0.9, 0.9),
        latch_score=None,
        updated_at=T0 + timedelta(hours=3),
    )
    rows = [only]
    assert pick_eval_row(rows, Decimal("0.85"), T0) is only
    assert pick_eval_row([], Decimal("0.85"), T0) is None


def _pair(a_hex: str, b_hex: str, wa: float, wb: float) -> PairEvalRow:
    return PairEvalRow(
        a=uuid.UUID(a_hex),
        b=uuid.UUID(b_hex),
        va=1,
        vb=1,
        jev_result=_jev(wa, wb),
    )


A = "00000000-0000-0000-0000-00000000000a"
B = "00000000-0000-0000-0000-00000000000b"
C = "00000000-0000-0000-0000-00000000000c"


def test_select_versioned_pairs_filters_by_version():
    """versions一致のペア行のみ(UNIQUE(a,b,va,vb)で高々1行・引用#25)。"""
    va2 = PairEvalRow(
        a=uuid.UUID(A),
        b=uuid.UUID(B),
        va=2,
        vb=1,
        jev_result=_jev(0.5, 0.5),
    )
    rows = [_pair(A, B, 0.9, 0.8), va2, _pair(A, C, 0.7, 0.7), _pair(B, C, 0.6, 0.6)]
    versions = {uuid.UUID(A): 1, uuid.UUID(B): 1, uuid.UUID(C): 1}
    got = select_versioned_pairs(rows, versions)
    assert len(got) == 3  # 3人=3ペアすべて現行世代
    assert all(r.va == 1 for r in got)


def test_pick_min_pair_minimum_mutual_with_tiebreak():
    """MutualScore最小ペア。同点は(a,b)辞書順最小で決定的(design §2.12)。"""
    ab = _pair(A, B, 0.9, 0.8)  # mutual 0.8
    ac = _pair(A, C, 0.6, 0.9)  # mutual 0.6 ← 最小
    bc = _pair(B, C, 0.7, 0.7)  # mutual 0.7
    assert pick_min_pair([ab, ac, bc]) is ac
    # 同点(双方mutual 0.6)は辞書順最小(a,b)組
    ab2 = _pair(A, B, 0.6, 0.9)
    ac2 = _pair(A, C, 0.6, 0.9)
    assert pick_min_pair([ac2, ab2]) is ab2
    assert pick_min_pair([]) is None


def test_build_prediction_shape():
    """prediction=would_*・MutualScore=min・L・jev_5axis・provider/model・segment(引用#13/#14)。"""
    got = build_prediction(_jev(0.9, 0.7), l_score=0.85, segment="lexical")
    assert got["would_a_accept_b"] == 0.9
    assert got["would_b_accept_a"] == 0.7
    assert got["MutualScore"] == 0.7
    assert got["L"] == 0.85
    assert got["jev_5axis"]["purpose_fit"]["value"] == 0.5
    assert got["provider"] == "typesafe_jev"
    assert got["model"] == "jev-1.13.0"
    assert got["segment"] == "lexical"
