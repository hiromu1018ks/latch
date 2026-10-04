"""stub遅延分布の決定的ハッシュ割付(M4 ws-6・design §2.2案A・10 §4.1)。

分位点の期待値は計画時に実計算した固定key集合の値(計画書 §2.4)。
割付は50%→p50値・43.75%→中間値・6.25%→p95値で・下位累積93.75%<95%の
ためnearest-rankのp95ランクは常にp95値グループに含まれる。
"""

from latch.llm.stub import StubLLM, distributed_delay_ms

# 計画書 §2.4の機械検証値(固定key集合・実計算済み)
_KEYS_10K = [str(i) for i in range(10000)]
_EXPECTED_COUNTS_1000_2500 = {1000: 5058, 1750: 4329, 2500: 613}


def test_distributed_delay_is_deterministic():
    """同一key→同一遅延(10 §1決定性)。"""
    assert distributed_delay_ms(1000, 2500, "same-input") == distributed_delay_ms(
        1000, 2500, "same-input"
    )


def test_distributed_delay_percentiles_match_targets():
    """固定key集合10万点弱の分位点が注入値と一致(§2.4の機械検証値)。"""
    delays = sorted(distributed_delay_ms(1000, 2500, k) for k in _KEYS_10K)
    counts = {}
    for d in delays:
        counts[d] = counts.get(d, 0) + 1
    assert counts == _EXPECTED_COUNTS_1000_2500
    assert delays[4999] == 1000  # nearest-rank p50 = 1000ms
    assert delays[9499] == 2500  # nearest-rank p95 = 2500ms


def test_distributed_delay_jev_targets_proportionally():
    """p50=2000/p95=5000も同一割付で比例(§2.4の2例目・load既定値)。"""
    delays = sorted(distributed_delay_ms(2000, 5000, k) for k in _KEYS_10K)
    counts = {}
    for d in delays:
        counts[d] = counts.get(d, 0) + 1
    assert counts == {2000: 5058, 3500: 4329, 5000: 613}
    assert delays[4999] == 2000
    assert delays[9499] == 5000


def test_stub_prefers_distribution_over_fixed_ms():
    """分布env非0 > 既存固定ms(優先順位・design §2.2案A)。"""
    stub = StubLLM(
        delay_jev_ms=50,
        latency_jev_p50_ms=2000,
        latency_jev_p95_ms=5000,
    )
    # 固定50msではなく分布割付の値(2000/3500/5000のいずれか)を返す
    assert stub._delay_ms("jev", "a|b") in (2000, 3500, 5000)


def test_stub_uses_fixed_ms_when_distribution_disabled():
    """分布0のときは既存固定ms経路を維持(ci挙動不変の根拠)。"""
    stub = StubLLM(delay_jev_ms=50)
    assert stub._delay_ms("jev", "a|b") == 50
    assert stub._delay_ms("jev") == 50  # keyなし呼び出し(既存形)


def test_stub_zero_delay_when_both_disabled():
    stub = StubLLM()
    assert stub._delay_ms("jev", "a|b") == 0
    assert stub._delay_ms("intent_parser", "t") == 0


def test_stub_judge_key_is_pair_of_texts():
    """jevの分布keyはペアテキスト(同一ペア→同一遅延)。"""
    stub = StubLLM(latency_jev_p50_ms=2000, latency_jev_p95_ms=5000)
    key_a = "intent-A-text|intent-B-text"
    key_b = "intent-A-text|intent-B-text"
    assert stub._delay_ms("jev", key_a) == stub._delay_ms("jev", key_b)


def test_stub_parser_key_is_input_text():
    """parserの分布keyは入力テキスト。"""
    stub = StubLLM(latency_parser_p50_ms=1000, latency_parser_p95_ms=2500)
    assert stub._delay_ms("intent_parser", "user raw text") in (1000, 1750, 2500)


def test_stub_embedding_has_no_distribution():
    """embeddingは分布を作らない(10 §4.1に注入規定なし・YAGNI)。"""
    stub = StubLLM(
        delay_embedding_ms=30,
        latency_parser_p50_ms=1000,
        latency_parser_p95_ms=2500,
        latency_jev_p50_ms=2000,
        latency_jev_p95_ms=5000,
    )
    assert stub._delay_ms("embedding", "any text") == 30


async def test_stub_judge_delay_runs_through_distribution():
    """judge呼び出しが分布遅延で決定的に完了する(実時間のassertは既存
    test_stub.pyの実測試験が所有するため・ここでは例外なし完了のみ)。"""
    stub = StubLLM(
        delay_jev_ms=0,
        latency_jev_p50_ms=30,
        latency_jev_p95_ms=60,
    )
    # 分布値(30/45/60ms)のいずれかで2回とも例外なく完了する
    await stub.judge("a", "b")
    await stub.judge("a", "b")
