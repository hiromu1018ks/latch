"""StubLLM: ABC実装・決定性・デフォルト応答・次元(design §4-4)。"""

from datetime import date
from time import perf_counter  # 実時間計測はテストコードのみ(design §4)

import pytest

from latch.llm.errors import LLMError, LLMProviderError
from latch.llm.providers import (
    EMBEDDING_DIMENSIONS,
    EmbeddingProvider,
    JevProvider,
    ParserProvider,
)
from latch.llm.stub import DEFAULT_JEV_RESPONSE, DEFAULT_PARSER_RESPONSE, StubLLM

# 07 第2節 出力JSONスキーマの全キー
PARSER_KEYS = {
    "category",
    "alcohol_involved",
    "time",
    "location",
    "budget",
    "participants",
    "soft_constraints",
    "negative_constraints",
    "ng_unverifiable",
}
# 07 第4節 出力JSONスキーマの全キー(7設問)
JEV_KEYS = {
    "would_a_accept_b",
    "would_b_accept_a",
    "purpose_fit",
    "mood_fit",
    "timing_fit",
    "social_fit",
    "latent_yes",
}


def test_provider_abcs_cannot_be_instantiated():
    # ABC: 実装漏れを静かに防ぐ(design §2.2・Clockと同じ判断)
    with pytest.raises(TypeError):
        ParserProvider()  # type: ignore[abstract]
    with pytest.raises(TypeError):
        EmbeddingProvider()  # type: ignore[abstract]
    with pytest.raises(TypeError):
        JevProvider()  # type: ignore[abstract]


def test_stub_implements_three_abcs():
    stub = StubLLM()
    assert isinstance(stub, ParserProvider)
    assert isinstance(stub, EmbeddingProvider)
    assert isinstance(stub, JevProvider)
    assert stub.name == "stub"  # 送信記録のdestination(design §3.1)


async def test_stub_parser_default_response_matches_07_section2():
    resp = await StubLLM().complete_structured(
        "今週末20時から軽く飲みたい", date(2026, 9, 27)
    )
    assert set(resp) == PARSER_KEYS
    # design §2.5: 必須3フィールド(category/time.start/location.name)を含む
    assert resp["category"]["primary"] in ("meal", "drinking", "activity")
    assert resp["time"]["start"]
    assert resp["location"]["name"]


async def test_stub_jev_default_response_matches_10_section1():
    resp = await StubLLM().judge(
        "Intent A:\n[hard] category: drinking", "Intent B:\n[hard] category: meal"
    )
    assert set(resp) == {"model", "answers", "usage"}  # System One envelope(10 §1 v0.4)
    assert set(resp["answers"]) == JEV_KEYS
    assert resp["model"] == "jev-1.13.0"
    assert resp["answers"]["would_a_accept_b"] == {"type": "noul", "noul": 0.5}
    assert resp["answers"]["purpose_fit"] == {
        "type": "score",
        "score": 2.0,
        "confidence": 0.5,
    }
    assert resp["usage"] == {"input_tokens": 0, "output_tokens": 0}


async def test_stub_embed_returns_deterministic_768_vector():
    stub = StubLLM()
    text = "meal / 平日夜20-23時 / 東京駅 / 2人 / 軽く"
    v1 = await stub.embed(text)
    v2 = await stub.embed(text)
    # Review Focus #4: 次元は768(05 第2節 vector(768)。C11)であることを二重検証
    assert len(v1) == EMBEDDING_DIMENSIONS
    assert EMBEDDING_DIMENSIONS == 768
    assert v1 == v2  # 決定性(10 第1節): 同一入力・同一設定→同一応答
    assert all(isinstance(x, float) for x in v1[:8])


async def test_stub_embed_is_input_derived():
    # 入力由来(ハッシュ)の生成であること —
    # 全入力が同一ベクトルではM2以降の試験ができない
    stub = StubLLM()
    v_a = await stub.embed("meal / 東京駅")
    v_b = await stub.embed("drinking / 天文館")
    assert v_a != v_b


def test_default_response_constants_match_spec():
    assert set(DEFAULT_PARSER_RESPONSE) == PARSER_KEYS
    assert set(DEFAULT_JEV_RESPONSE) == {"model", "answers", "usage"}  # envelope


async def test_stub_responses_are_deterministic_across_instances():
    # 同一設定の別インスタンスでも同一応答(乱数・実時間参照なしの証明)
    text = "今週末20時から駅前で軽く飲みたい"
    a = await StubLLM().complete_structured(text, date(2026, 9, 27))
    b = await StubLLM().complete_structured(text, date(2026, 9, 27))
    assert a == b
    assert a == DEFAULT_PARSER_RESPONSE


async def test_stub_response_override():
    # テストが任意の応答・検証失敗応答を注入できる(design §2.5)
    bad_parser = {"category": None}  # M1のスキーマ検証失敗再現の例
    got = await StubLLM(parser_response=bad_parser).complete_structured(
        "t", date(2026, 1, 1)
    )
    assert got == bad_parser
    fixed = [0.25] * 768
    assert await StubLLM(embedding_response=fixed).embed("t") == fixed
    bad_jev = {"model": "jev-1.13.0", "answers": {"would_a_accept_b": {}}, "usage": {}}
    assert await StubLLM(jev_response=bad_jev).judge("a", "b") == bad_jev


async def test_stub_delay_injects_real_latency():
    # design §2.6: 系統別の固定遅延。呼び出し前にasyncio.sleepする(時刻参照ではない)
    stub = StubLLM(delay_jev_ms=50)
    start = perf_counter()
    await stub.judge("a", "b")
    assert (perf_counter() - start) * 1000 >= 50


async def test_stub_delay_is_per_system():
    stub = StubLLM(delay_jev_ms=50)
    start = perf_counter()
    await stub.embed("t")  # embeddingは遅延なし → 即返る
    elapsed_ms = (perf_counter() - start) * 1000
    assert elapsed_ms < 50


async def test_stub_zero_delay_by_default():
    stub = StubLLM()
    start = perf_counter()
    await stub.complete_structured("t", date(2026, 1, 1))
    await stub.embed("t")
    await stub.judge("a", "b")
    assert (perf_counter() - start) * 1000 < 50  # 既定は無効(10 第1節)


async def test_stub_fail_flags_raise_provider_error():
    # design §2.6 / 10 第4.5節: 100%エラー注入の再現
    with pytest.raises(LLMProviderError):
        await StubLLM(fail_parser=True).complete_structured("t", date(2026, 1, 1))
    with pytest.raises(LLMProviderError):
        await StubLLM(fail_embedding=True).embed("t")
    with pytest.raises(LLMProviderError):
        await StubLLM(fail_jev=True).judge("a", "b")


async def test_stub_fail_is_per_system():
    stub = StubLLM(fail_parser=True)
    with pytest.raises(LLMProviderError):
        await stub.complete_structured("t", date(2026, 1, 1))
    assert len(await stub.embed("t")) == 768  # 他系統は影響を受けない


async def test_stub_responses_are_not_shared_mutable():
    # 消費者が応答を書き換えてもデフォルト応答(他インスタンス・他試験)が
    # 汚染されない — 決定性(10 第1節)の前提を守る防御的コピー。
    stub = StubLLM()
    resp = await stub.complete_structured("t", date(2026, 1, 1))
    resp["category"]["primary"] = "POLLUTED"
    resp["time"]["start"] = None
    # 同一インスタンスの再呼び出しも汚染されない
    again = await stub.complete_structured("t", date(2026, 1, 1))
    assert again["category"]["primary"] == "meal"
    assert again["time"]["start"] == "2026-09-27T19:00:00+09:00"
    # 他インスタンス・モジュール定数も汚染されない
    fresh = await StubLLM().complete_structured("t", date(2026, 1, 1))
    assert fresh["category"]["primary"] == "meal"
    assert DEFAULT_PARSER_RESPONSE["category"]["primary"] == "meal"

    jev_stub = StubLLM()
    jev = await jev_stub.judge("a", "b")
    jev["answers"]["purpose_fit"]["score"] = 0.99
    assert (await jev_stub.judge("a", "b"))["answers"]["purpose_fit"]["score"] == 2.0

    fixed = [0.25] * 768
    embed_stub = StubLLM(embedding_response=fixed)
    (await embed_stub.embed("t"))[0] = 0.99
    assert (await embed_stub.embed("t"))[0] == 0.25


def test_switch_exceptions_are_llm_error_subclasses():
    """切替条件例外4種はLLMError継承(07 §4切替表・design §2.2)。"""
    from latch.llm.errors import (
        JevOutputInvalidError,
        LLMConnectionError,
        LLMOverloadedError,
        LLMRateLimitError,
    )

    for exc in (LLMRateLimitError, LLMOverloadedError, LLMConnectionError, JevOutputInvalidError):
        assert issubclass(exc, LLMError)
