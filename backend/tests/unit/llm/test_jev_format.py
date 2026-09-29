"""llm/jev.py(正規化テキスト・出力検証・質問定数)のunit試験(design §4.1)。"""

from dataclasses import fields
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace

import pytest

from latch.llm.errors import JevOutputInvalidError
from latch.llm.jev import (
    JEV_MODEL,
    JEV_QUESTIONS,
    JEV_RESULT_KEYS,
    NOUL_KEYS,
    SCORE_KEYS,
    JevTextInput,
    build_jev_text,
    validate_and_normalize,
)

T0 = datetime(2026, 9, 26, 11, 0, 0, tzinfo=UTC)  # JST 2026-09-26 20:00
T1 = T0 + timedelta(hours=3)


def _inp(**over) -> JevTextInput:
    base = dict(
        category_primary="drinking",
        structured_data={
            "location_name": "天文館周辺",
            "soft_constraints": [
                {"text": "軽く飲みたい", "downgraded_from_ng": False},
                {"text": "会社関係の人は避けたい", "downgraded_from_ng": True},
            ],
        },
        participants_min=2,
        participants_max=4,
        time_start=T0,
        time_end=T1,
        budget_max=5000,
        geo_radius_m=2000,
    )
    base.update(over)
    return JevTextInput(**base)


def _answers(**over) -> dict:
    answers = {
        "would_a_accept_b": {"type": "noul", "noul": 0.83},
        "would_b_accept_a": {"type": "noul", "noul": 0.71},
        "latent_yes": {"type": "noul", "noul": 0.40},
        "purpose_fit": {"type": "score", "score": 3.0, "confidence": 0.8},
        "mood_fit": {"type": "score", "score": 2.0, "confidence": 0.6},
        "timing_fit": {"type": "score", "score": 2.0, "confidence": 0.7},
        "social_fit": {"type": "score", "score": 3.0, "confidence": 0.6},
    }
    answers.update(over)
    return answers


def _envelope(**over) -> dict:
    answers = _answers(**over.pop("answers", {}))
    return {"model": "jev-1.13.0", "answers": answers, "usage": {}}


# -- 質問定数のピン(07 §4実装イメージの全文ピン・変更検知)--

_EXPECTED_QUESTIONS = {
    "would_a_accept_b": {
        "type": "noul",
        "instructions": {
            "a": "`state.intent_a`",
            "b": "`state.intent_b`",
            "question": "`a`の作成者が`b`との成立を提示されたときyesと答える確率。"
            "`a`の[hard]条件を満たすか、`a`の[soft]条件と判定不能NG条件に"
            "触れないことを含めて判定する。`b`が`a`の[hard]条件と意味的に"
            "矛盾する場合(予算感の著しい乖離が食事内容を成立させない等)は"
            "yesから遠ざける",
        },
        "criteria": {
            "true": "成立し得る提案である",
            "false": "成立しない提案である",
        },
    },
    "would_b_accept_a": {
        "type": "noul",
        "instructions": {
            "a": "`state.intent_b`",
            "b": "`state.intent_a`",
            "question": "`a`の作成者が`b`との成立を提示されたときyesと答える確率。"
            "`a`の[hard]条件を満たすか、`a`の[soft]条件と判定不能NG条件に"
            "触れないことを含めて判定する。`b`が`a`の[hard]条件と意味的に"
            "矛盾する場合(予算感の著しい乖離が食事内容を成立させない等)は"
            "yesから遠ざける",
        },
        "criteria": {
            "true": "成立し得る提案である",
            "false": "成立しない提案である",
        },
    },
    "purpose_fit": {
        "type": "score",
        "instructions": "`a`と`b`の目的・カテゴリの適合度(飲みたい×食べたいのズレ等)",
        "criteria": [
            "目的が根本的に異なる",
            "目的が近いが核心がずれる",
            "目的が部分的に重なる",
            "ほぼ同一目的",
            "同一目的",
        ],
    },
    "mood_fit": {
        "type": "score",
        "instructions": "`a`と`b`の雰囲気・軽さの適合度(「軽く」×「がっつり」等)",
        "criteria": [
            "雰囲気が相容れない",
            "重さが大きく異なる",
            "どちらでも成立する",
            "重さが近い",
            "雰囲気が同一",
        ],
    },
    "timing_fit": {
        "type": "score",
        "instructions": "`a`と`b`の時間帯・所要時間の適合度",
        "criteria": [
            "時間帯が合わない",
            "開始は合うが所要が合わない",
            "開始・所要が部分的に重なる",
            "時間帯・所要が近い",
            "同一時間帯",
        ],
    },
    "social_fit": {
        "type": "score",
        "instructions": "`a`と`b`の人数・社会的文脈(立場・関係性)の適合度",
        "criteria": [
            "人数・文脈が成立しない",
            "人数は合うが社会的文脈がずれる",
            "人数・文脈が部分的に合う",
            "人数・文脈が近い",
            "人数・文脈が同一",
        ],
    },
    "latent_yes": {
        "type": "noul",
        "instructions": "どちらかが明示していないが、そのIntentの記述の範囲内で"
        "YESになり得る可能性(「焼肉に行きたい」×「今日は肉系ならどこでもいい」等)",
    },
}


def test_jev_questions_full_pin():
    assert JEV_QUESTIONS == _EXPECTED_QUESTIONS
    assert JEV_MODEL == "jev-1.13.0"  # エイリアス不使用(07 §4)


def test_jev_questions_structure_pin():
    assert set(JEV_QUESTIONS) == set(JEV_RESULT_KEYS)
    for key in ("would_a_accept_b", "would_b_accept_a"):
        assert JEV_QUESTIONS[key]["type"] == "noul"
        assert isinstance(JEV_QUESTIONS[key]["criteria"], dict)
    for key in SCORE_KEYS:
        assert JEV_QUESTIONS[key]["type"] == "score"
        assert isinstance(JEV_QUESTIONS[key]["criteria"], list)
        assert len(JEV_QUESTIONS[key]["criteria"]) == 5
    assert JEV_QUESTIONS["latent_yes"]["type"] == "noul"
    assert "criteria" not in JEV_QUESTIONS["latent_yes"]  # instructionsのみ
    assert set(NOUL_KEYS) == {"would_a_accept_b", "would_b_accept_a", "latent_yes"}


# -- build_jev_text(07 §4例の再現・行規則ピン) --


def test_build_jev_text_reproduces_07_example():
    assert build_jev_text(_inp(), label="Intent A") == (
        "Intent A:\n"
        "[hard] category: drinking\n"
        "[hard] time: 2026-09-26 20:00–23:00\n"
        "[hard] location: 天文館周辺 半径2km\n"
        "[hard] participants: 2–4人\n"
        "[hard] budget_max: 5000円\n"
        "[soft] 軽く飲みたい\n"
        "[soft] 会社関係の人は避けたい(システムで判定不能)"
    )


def test_build_jev_text_omits_budget_when_none():
    out = build_jev_text(_inp(budget_max=None), label="Intent A")
    assert "[hard] budget_max" not in out
    assert "[hard] category: drinking" in out  # 他行は残る


def test_build_jev_text_omits_location_when_name_empty():
    out = build_jev_text(
        _inp(structured_data={"soft_constraints": []}), label="Intent A"
    )
    assert "[hard] location" not in out


def test_build_jev_text_radius_m_when_not_multiple_of_1000():
    assert "半径1500m" in build_jev_text(_inp(geo_radius_m=1500), label="Intent A")
    assert "半径500m" in build_jev_text(_inp(geo_radius_m=500), label="Intent A")


def test_build_jev_text_participants_equal_shows_single_value():
    out = build_jev_text(_inp(participants_min=2, participants_max=2), label="Intent A")
    assert "[hard] participants: 2人" in out


def test_build_jev_text_omits_time_when_start_none():
    out = build_jev_text(_inp(time_start=None, time_end=None), label="Intent A")
    assert "[hard] time" not in out


def test_build_jev_text_no_soft_lines_when_constraints_empty():
    sd = {"location_name": "天文館周辺", "soft_constraints": []}
    out = build_jev_text(_inp(structured_data=sd), label="Intent A")
    assert "[soft]" not in out


def test_build_jev_text_label_b():
    assert build_jev_text(_inp(), label="Intent B").startswith("Intent B:")


def test_input_excludes_forbidden_fields():
    """raw_text・visibility等はJev入力に含めない(07 §4・01 §21・構造的ピン)。"""
    names = {f.name for f in fields(JevTextInput)}
    forbidden = {
        "raw_text",
        "visibility",
        "notification_level",
        "alcohol_involved",
        "category_secondary",
    }
    assert not (forbidden & names)


# -- validate_and_normalize(出力検証・正規化) --


async def test_normalize_returns_result_part():
    result = validate_and_normalize(_envelope())
    assert result["would_a_accept_b"] == 0.83
    assert result["would_b_accept_a"] == 0.71
    axis = result["jev_5axis"]
    assert axis["purpose_fit"] == {"value": 0.75, "confidence": 0.8}  # 3.0/4
    assert axis["latent_yes"] == {"value": 0.40, "confidence": None}  # noulに付かない
    assert set(result) == {"would_a_accept_b", "would_b_accept_a", "jev_5axis"}
    assert "provider" not in result and "model" not in result  # Gatewayが載せる


def test_normalize_score_boundaries():
    env0 = _envelope(
        answers={"purpose_fit": {"type": "score", "score": 0.0, "confidence": None}}
    )
    assert validate_and_normalize(env0)["jev_5axis"]["purpose_fit"]["value"] == 0.0
    env4 = _envelope(
        answers={"mood_fit": {"type": "score", "score": 4.0, "confidence": 0.5}}
    )
    assert validate_and_normalize(env4)["jev_5axis"]["mood_fit"]["value"] == 1.0  # /4
    env1 = _envelope(
        answers={"mood_fit": {"type": "score", "score": 1.035, "confidence": None}}
    )
    assert validate_and_normalize(env1)["jev_5axis"]["mood_fit"]["value"] == 1.035 / 4


def test_normalize_score_out_of_range_raises():
    env = _envelope(
        answers={"purpose_fit": {"type": "score", "score": 4.1, "confidence": 0.5}}
    )
    with pytest.raises(JevOutputInvalidError):
        validate_and_normalize(env)


def test_normalize_missing_key_raises():
    env = _envelope()
    del env["answers"]["latent_yes"]
    with pytest.raises(JevOutputInvalidError):
        validate_and_normalize(env)


def test_normalize_extra_key_raises():
    env = _envelope()
    env["answers"]["extra"] = {"type": "noul", "noul": 0.5}
    with pytest.raises(JevOutputInvalidError):
        validate_and_normalize(env)


@pytest.mark.parametrize(
    ("key", "value"),
    [
        ("would_a_accept_b", 1.1),
        ("would_a_accept_b", -0.01),
        ("latent_yes", 1.1),
    ],
)
def test_normalize_noul_out_of_range_raises(key, value):
    env = _envelope(answers={key: {"type": "noul", "noul": value}})
    with pytest.raises(JevOutputInvalidError):
        validate_and_normalize(env)


def test_normalize_score_negative_raises():
    env = _envelope(
        answers={"purpose_fit": {"type": "score", "score": -0.1, "confidence": 0.5}}
    )
    with pytest.raises(JevOutputInvalidError):
        validate_and_normalize(env)


def test_normalize_string_value_raises():
    env = _envelope(answers={"would_a_accept_b": {"type": "noul", "noul": "0.5"}})
    with pytest.raises(JevOutputInvalidError):
        validate_and_normalize(env)


def test_normalize_bool_excluded():
    """boolは実数として除外(Trueが1.0扱いになる事故の防止)。"""
    env = _envelope(answers={"would_a_accept_b": {"type": "noul", "noul": True}})
    with pytest.raises(JevOutputInvalidError):
        validate_and_normalize(env)


def test_normalize_answers_not_dict_raises():
    env = _envelope()
    env["answers"] = "no"
    with pytest.raises(JevOutputInvalidError):
        validate_and_normalize(env)


def test_normalize_envelope_not_dict_raises():
    with pytest.raises(JevOutputInvalidError):
        validate_and_normalize("x")


def test_normalize_confidence_out_of_range_raises():
    env = _envelope(
        answers={"purpose_fit": {"type": "score", "score": 2.0, "confidence": 1.1}}
    )
    with pytest.raises(JevOutputInvalidError):
        validate_and_normalize(env)


def test_normalize_confidence_string_raises():
    env = _envelope(
        answers={"purpose_fit": {"type": "score", "score": 2.0, "confidence": "x"}}
    )
    with pytest.raises(JevOutputInvalidError):
        validate_and_normalize(env)


def test_normalize_fallback_envelope_all_confidence_none():
    """フォールバックLLM応答はconfidenceを持たない(envelopeではNone)。"""
    env = _envelope()
    for key in SCORE_KEYS:
        env["answers"][key]["confidence"] = None
    result = validate_and_normalize(env)
    for key in SCORE_KEYS:
        assert result["jev_5axis"][key]["confidence"] is None
    assert result["would_a_accept_b"] == 0.83


def test_normalize_object_answers_pass():
    """dictでないanswer(dataclass等)も_field経由で検証通過(型両対応)。"""
    env = _envelope(
        answers={
            "would_a_accept_b": SimpleNamespace(noul=0.5),
            "would_b_accept_a": SimpleNamespace(noul=0.5),
            "latent_yes": SimpleNamespace(noul=0.5),
            "purpose_fit": SimpleNamespace(score=2.0, confidence=None),
            "mood_fit": SimpleNamespace(score=2.0, confidence=None),
            "timing_fit": SimpleNamespace(score=2.0, confidence=None),
            "social_fit": SimpleNamespace(score=2.0, confidence=None),
        }
    )
    result = validate_and_normalize(env)
    assert result["would_a_accept_b"] == 0.5
    assert result["jev_5axis"]["purpose_fit"] == {"value": 0.5, "confidence": None}
