"""layer3(cheap_score純関数群)のunit試験(design §4.1)。

すべて境界値を手計算と照合する決定的検査(06 §4の決定性要件・
同一入力→同一出力)。実DBとの組み合わせはintegration(test-ci)が担う。
"""

import json
import uuid
from datetime import UTC, datetime, timedelta

import pytest

from latch.worker.matching.layer3 import (
    BUDGET_SATURATION_YEN,
    K_CHEAP,
    NEUTRAL,
    TIME_SATURATION_MIN,
    ScoredCandidate,
    _bigrams,
    budget_closeness,
    cheap_score,
    rule_score,
    select_top_kc,
    soft_texts,
    time_closeness,
    vocab_overlap,
)

T0 = datetime(2026, 9, 29, 12, 0, 0, tzinfo=UTC)


def _uid(n: int) -> uuid.UUID:
    return uuid.UUID(f"00000000-0000-4000-8000-{n:012d}")


def test_constants_pin_docs_values():
    """docs確定値のピン(§9固定値)。"""
    assert K_CHEAP == 20  # 06 §8 D-24
    assert TIME_SATURATION_MIN == 180  # design §2.2(Intent実効寿命3時間=03 D-19)
    assert BUDGET_SATURATION_YEN == 3000  # design §2.2初期値
    assert NEUTRAL == 0.5


def test_time_closeness_boundaries():
    """Δ0=1.0・Δ180=0.0・Δ90=0.5・Δ240=0.0(飽和)・対称性。"""
    assert time_closeness(T0, T0) == 1.0
    assert time_closeness(T0, T0 + timedelta(minutes=180)) == 0.0
    assert time_closeness(T0, T0 + timedelta(minutes=90)) == 0.5
    assert time_closeness(T0, T0 + timedelta(minutes=240)) == 0.0
    assert time_closeness(T0 + timedelta(minutes=240), T0) == 0.0


def test_budget_closeness_boundaries():
    """差0=1.0・差3000=0.0・差1500=0.5・NULL系=中立0.5(design §2.2)。"""
    assert budget_closeness(3000, 3000) == 1.0
    assert budget_closeness(3000, 0) == 0.0
    assert budget_closeness(3000, 1500) == 0.5
    assert budget_closeness(3000, 7000) == 0.0  # 差4000で飽和(3000超)
    assert budget_closeness(None, 1000) == NEUTRAL
    assert budget_closeness(1000, None) == NEUTRAL
    assert budget_closeness(None, None) == NEUTRAL


def test_rule_score_is_mean_of_two():
    """ルールスコア=(時間近さ+予算近さ)/2(design §2.2)。"""
    rule = rule_score(
        origin_time_start=T0,
        cand_time_start=T0 + timedelta(minutes=90),  # 0.5
        origin_budget=3000,
        cand_budget=1500,  # 0.5
    )
    assert rule == 0.5
    rule2 = rule_score(
        origin_time_start=T0,
        cand_time_start=T0,  # 1.0
        origin_budget=None,
        cand_budget=None,  # 0.5(中立)
    )
    assert rule2 == 0.75


def test_bigrams_nfkc_short_and_no_cross_text():
    """NFKC正規化・2文字未満は空・textをまたいだbigramを作らない。"""
    assert _bigrams(("焼肉",)) == frozenset({"焼肉"})
    assert _bigrams(("あ",)) == frozenset()  # 2文字未満
    assert _bigrams(()) == frozenset()
    assert _bigrams(("ＡＢ",)) == _bigrams(("AB",))  # NFKC: 全角→半角
    assert _bigrams(("AB", "CD")) == frozenset({"AB", "CD"})
    assert "BC" not in _bigrams(("AB", "CD"))  # またがない


def test_vocab_overlap_jaccard():
    """同一文言=1.0・共通なし=0.0・部分一致=手計算値・空の扱い。"""
    assert vocab_overlap(("焼肉",), ("焼肉",)) == 1.0
    assert vocab_overlap(("焼肉",), ("寿司",)) == 0.0
    # design §4.1の手計算例: {焼肉} vs {焼肉,肉好,好き} = 1/3
    assert vocab_overlap(("焼肉",), ("焼肉好き",)) == pytest.approx(1 / 3)
    assert vocab_overlap((), ()) == NEUTRAL  # 双方空=中立(design §2.2)
    assert vocab_overlap(("焼肉",), ()) == 0.0  # 片方空=共通なし


def test_soft_texts_excludes_downgraded():
    """downgraded_from_ng=trueの文言は抽出しない(06 §4・引用#1)。"""
    sd = {
        "soft_constraints": [
            {"text": "焼肉", "downgraded_from_ng": False},
            {"text": "個室", "downgraded_from_ng": True},
            {"text": "静か"},  # キー欠落=降格ではない
        ]
    }
    assert soft_texts(sd) == ("焼肉", "静か")
    assert soft_texts(json.dumps(sd)) == ("焼肉", "静か")  # jsonbのstr返り
    assert soft_texts(None) == ()
    assert soft_texts("{}") == ()
    assert soft_texts('{"soft_constraints": []}') == ()
    assert soft_texts('{"soft_constraints": "not-a-list"}') == ()


def test_cheap_score_weights_pin():
    """0.5/0.3/0.2 の手計算照合(06 §4)・丸めなし。"""
    assert cheap_score(1.0, 1.0, 1.0) == 1.0
    assert cheap_score(0.0, 0.0, 0.0) == 0.0
    expected = 0.5 * 0.8 + 0.3 * 0.5 + 0.2 * (1 / 3)
    assert cheap_score(0.8, 0.5, 1 / 3) == expected  # 同一式=完全同一float


def test_select_top_kc_tie_break():
    """降順・同点はintent_id昇順(D-24)。"""
    scored = [
        ScoredCandidate(_uid(30), 1, 1.0, 0.9),
        ScoredCandidate(_uid(10), 1, 1.0, 0.5),  # 同点グループ
        ScoredCandidate(_uid(2), 1, 1.0, 0.5),
        ScoredCandidate(_uid(20), 1, 1.0, 0.5),
        ScoredCandidate(_uid(1), 1, 1.0, 0.1),
    ]
    top = select_top_kc(scored)
    assert [s.intent_id for s in top] == [
        _uid(30),
        _uid(2),
        _uid(10),
        _uid(20),
        _uid(1),
    ]


def test_select_top_kc_truncates_at_20_deterministic():
    """21件以上で20件・同一入力2回で完全同一(10 §4.6・丸めなし)。"""
    scored = [ScoredCandidate(_uid(i), 1, 1.0, 1.0 - i * 0.001) for i in range(25)]
    top = select_top_kc(scored)
    assert len(top) == 20
    assert top[0].intent_id == _uid(0)
    assert top[-1].intent_id == _uid(19)
    again = select_top_kc(scored)
    assert again == top  # dataclass完全一致(順序・score含む)
