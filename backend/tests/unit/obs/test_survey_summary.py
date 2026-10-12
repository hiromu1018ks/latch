"""obs.survey(体感アンケート集計・05 v0.7)のunit試験。

純関数summarizeの期待値表(仮説1=week4のconvenience平均4.0以上・
仮説5=continuation4点以上が過半)を固定する。09 §2.3の事前固定値。
"""

from __future__ import annotations

from latch.obs.survey import summarize


def _row(round_, convenience, continuation=None, comment=None):
    answers = {"convenience": convenience}
    if continuation is not None:
        answers["continuation"] = continuation
    return {"round": round_, "answers": answers, "free_comment": comment}


def test_empty():
    out = summarize([])
    assert out["onboarding"]["n"] == 0
    assert out["onboarding"]["convenience_mean"] is None
    assert out["week4"]["n"] == 0
    # 判定フラグはweek4のみ(onboarding側には付かない)
    assert "hypothesis1_survey_pass" not in out["onboarding"]


def test_onboarding_mean():
    out = summarize(
        [_row("onboarding", 4), _row("onboarding", 5), _row("onboarding", 3)]
    )
    assert out["onboarding"]["n"] == 3
    assert out["onboarding"]["convenience_mean"] == 4.0
    assert out["onboarding"]["continuation_distribution"] == {
        "1": 0,
        "2": 0,
        "3": 0,
        "4": 0,
        "5": 0,
    }


def test_week4_hypothesis1_pass_and_hypothesis5_pass():
    rows = [
        _row("week4", 4, 5, "楽しい"),
        _row("week4", 5, 4),
        _row("week4", 4, 4, ""),
        _row("week4", 3, 4),
    ]
    out = summarize(rows)
    w = out["week4"]
    assert w["n"] == 4
    assert w["convenience_mean"] == 4.0
    assert w["hypothesis1_survey_pass"] is True  # 平均4.0以上
    assert w["continuation_ge4_count"] == 4
    assert w["hypothesis5_survey_pass"] is True  # 4点以上=4/4>2
    assert w["free_comment_count"] == 1  # 空文字はコメントなし扱い
    assert w["continuation_distribution"]["4"] == 3
    assert w["continuation_distribution"]["5"] == 1


def test_week4_hypothesis_fail_values():
    rows = [
        _row("week4", 3, 2),
        _row("week4", 3, 4),
        _row("week4", 3, 4),
    ]
    out = summarize(rows)
    w = out["week4"]
    assert w["convenience_mean"] == 3.0
    assert w["hypothesis1_survey_pass"] is False
    # ge4=2/3は過半でない(2 > 1.5はTrue…ではなく「過半」は>n/2=1.5で成立する)
    # → 2 > 1.5 はTrueのためpass。境界の意図: 3人中2人(66%)は過半
    assert w["hypothesis5_survey_pass"] is True
    # 1/2ちょうど(=n/2)は過半でない
    out2 = summarize([_row("week4", 3, 5), _row("week4", 3, 2)])
    assert out2["week4"]["hypothesis5_survey_pass"] is False


def test_mixed_rounds_are_separated():
    out = summarize([_row("onboarding", 5), _row("week4", 4, 4)])
    assert out["onboarding"]["n"] == 1
    assert out["week4"]["n"] == 1
    assert out["onboarding"]["continuation_distribution"]["4"] == 0
    assert out["week4"]["continuation_distribution"]["4"] == 1
