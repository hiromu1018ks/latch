"""segment別YES率集計(A-04・09 §2.3仮説2)のunit試験(純関数・決定的)。"""

from __future__ import annotations

from datetime import date

from latch.obs.segment import (
    hypothesis2_verdict,
    iter_answers,
    segment_of_prediction,
    weekly_segment_yes,
    yes_rate,
)


def test_iter_answers_supports_raw_and_anonymized_forms():
    raw = [
        {"user_id": "u1", "value": "yes", "answered_at": "2026-10-05T20:00:00+09:00"},
        {"value": "defer", "answered_at": "2026-10-06T09:00:00+09:00"},
        {"value": "no", "answered_at": None},  # 日付不明=集計外
        {"value": "yes", "answered_date": "2026-10-07"},  # 匿名化済み形式
        {"value": "unknown", "answered_at": "2026-10-05T20:00:00+09:00"},  # 値域外
    ]
    out = iter_answers(raw)
    assert [v for v, _ in out] == ["yes", "defer", "yes"]
    assert [d.isoformat() for _, d in out] == ["2026-10-05", "2026-10-06", "2026-10-07"]
    # 文字列jsonb・破損も安全側
    assert iter_answers('[{"value":"yes","answered_at":"2026-10-05T20:00:00+09:00"}]')
    assert iter_answers("broken") == []
    assert iter_answers(None) == []


def test_iter_answers_jst_day_of_utc_midnight():
    """UTC 15:30前後=JST翌日。日付境界はJST暦日付で切る。"""
    out = iter_answers(
        [{"value": "yes", "answered_at": "2026-10-05T15:30:00+00:00"}]
    )  # JST 10/6 00:30
    assert out[0][1] == date(2026, 10, 6)


def test_segment_of_prediction():
    assert segment_of_prediction({"segment": "lexical"}) == "lexical"
    assert segment_of_prediction('{"segment": "semantic"}') == "semantic"
    assert segment_of_prediction({"segment": "other"}) is None
    assert segment_of_prediction({}) is None
    assert segment_of_prediction("broken") is None


def test_weekly_segment_yes_aggregates_within_week_only():
    week = date(2026, 10, 5)  # 月曜
    rows = [
        (
            {"segment": "lexical"},
            [
                {"value": "yes", "answered_at": "2026-10-05T20:00:00+09:00"},
                {"value": "no", "answered_at": "2026-10-06T21:00:00+09:00"},
            ],
        ),
        (
            {"segment": "semantic"},
            [{"value": "yes", "answered_at": "2026-10-11T23:00:00+09:00"}],
        ),
        # 週外(前週)は数えない
        (
            {"segment": "semantic"},
            [{"value": "yes", "answered_at": "2026-10-04T23:00:00+09:00"}],
        ),
        # segment不明は数えない
        (
            {},
            [{"value": "yes", "answered_at": "2026-10-07T12:00:00+09:00"}],
        ),
        # 匿名化済み形式も同週で数える
        (
            {"segment": "lexical"},
            [{"value": "yes", "answered_date": "2026-10-08"}],
        ),
    ]
    out = weekly_segment_yes(rows, week)
    assert out == {
        "lexical": {"yes": 2, "answers": 3},
        "semantic": {"yes": 1, "answers": 1},
    }
    # 空でも両キーは置く(欠測との区別)
    empty = weekly_segment_yes([], week)
    assert empty == {
        "lexical": {"yes": 0, "answers": 0},
        "semantic": {"yes": 0, "answers": 0},
    }


def test_yes_rate_none_on_zero_denominator():
    assert yes_rate({"yes": 3, "answers": 4}) == 0.75
    assert yes_rate({"yes": 0, "answers": 0}) is None


def test_hypothesis2_verdict():
    # 09 §2.3: semantic≥10% かつ lexicalの50%以上
    ok = hypothesis2_verdict(lexical_rate=0.40, semantic_rate=0.30)
    assert ok["verdict"] == "pass"
    assert ok["semantic_ge_10pct"] is True
    assert ok["semantic_ge_half_of_lexical"] is True
    # semantic<10%はfail(lexical半分を超えていても)
    low = hypothesis2_verdict(lexical_rate=0.90, semantic_rate=0.20)
    assert low["verdict"] == "fail"
    assert low["semantic_ge_10pct"] is True
    assert low["semantic_ge_half_of_lexical"] is False
    # lexical=0(測定不能)はno_data
    assert (
        hypothesis2_verdict(lexical_rate=None, semantic_rate=0.5)["verdict"]
        == "no_data"
    )
    assert (
        hypothesis2_verdict(lexical_rate=0.5, semantic_rate=None)["verdict"]
        == "no_data"
    )
