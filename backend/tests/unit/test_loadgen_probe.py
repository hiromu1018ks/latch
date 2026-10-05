"""プローブ集計の純関数・TokenCache・run_probeの決定性(M4 ws-7)。

成功率=成功プローブ数÷総プローブ数(main側・マーカータグ付きは分母外)。
合否は99.5%の機械判定(10 §4.3・01 §20)。実HTTPはunit対象外で・run_probeは
sender注入で決定的に検証する。
"""

import asyncio
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from latch.core.clock import FakeClock
from latch.loadgen.probe import ProbeSample, run_probe, summarize

NOW = datetime(2026, 10, 5, 9, 0, 0, tzinfo=UTC)


def _s(kind="main", ok=True, error=None, tagged=False, offset_s=0.0, path="/health"):
    return ProbeSample(
        kind=kind,
        at=NOW + timedelta(seconds=offset_s),
        path=path,
        ok=ok,
        error=error,
        tagged_planned_outage=tagged,
    )


def test_summarize_excludes_tagged_from_denominator():
    """マーカータグ付きサンプルは分母外(承認事項§5-2)。"""
    samples = [_s(ok=True, offset_s=i * 60) for i in range(6)] + [
        _s(ok=False, error="timeout", tagged=True, offset_s=400)
    ]
    result = summarize(samples)
    assert result["main_total"] == 7
    assert result["main_excluded_planned_outage"] == 1
    assert result["main_counted"] == 6
    assert result["main_ok"] == 6
    assert result["main_success_rate"] == 1.0
    assert result["verdict_995"] is True


def test_summarize_counts_timeout_as_failure():
    samples = [
        _s(ok=True),
        _s(ok=False, error="timeout"),
        _s(ok=False, error="HTTP 502"),
    ]
    result = summarize(samples)
    assert result["main_ok"] == 1
    assert result["main_error"] == 2
    assert result["main_success_rate"] == pytest.approx(1 / 3)
    assert result["verdict_995"] is False
    assert result["timeouts"] == 1
    assert result["errors_by_kind"] == {"timeout": 1, "HTTP 502": 1}


def test_summarize_records_max_consecutive_failures():
    """連続失敗最長の記録(開始/終了時刻つき)。"""
    samples = [
        _s(ok=True),
        _s(ok=False, error="timeout", offset_s=60),
        _s(ok=False, error="timeout", offset_s=120),
        _s(ok=True, offset_s=180),
    ]
    result = summarize(samples)
    assert result["max_consecutive_failures"]["count"] == 2
    assert (
        result["max_consecutive_failures"]["started_at"]
        == (NOW + timedelta(seconds=60)).isoformat()
    )
    assert (
        result["max_consecutive_failures"]["ended_at"]
        == (NOW + timedelta(seconds=120)).isoformat()
    )


def test_summarize_separates_dense_from_verdict():
    """dense側は合否対象外(design §2.1)。"""
    samples = [_s(ok=True), _s(kind="dense", ok=False, error="HTTP 503")]
    result = summarize(samples)
    assert result["verdict_995"] is True
    assert result["dense_total"] == 1
    assert result["dense_ok"] == 0


def test_summarize_empty_main_returns_no_verdict():
    result = summarize([])
    assert result["main_success_rate"] is None
    assert result["verdict_995"] is False


async def test_run_probe_collects_main_and_dense_samples():
    """main/dense両ループが指定間隔で回り・durationで終了する(sleep注入で決定的)。

    1tick=2サンプル(PROBE_PATHSの2パス・§8手順の「main=120」=60ticks×2パス
    と同一の数え方・計画テストコードの欠陥修正でtick数からサンプル数へ訂正)。
    """
    clock = FakeClock(NOW)

    async def fake_sleep(seconds: float) -> None:
        clock.advance(timedelta(seconds=seconds))

    async def fake_request(path: str, bearer: str):
        clock.advance(timedelta(milliseconds=50))

    samples = await run_probe(
        http=None,
        base_url="http://test",
        token_cache=None,
        duration_s=5.0,
        clock=clock,
        main_interval_s=2.0,
        dense_interval_s=1.0,
        request_path=fake_request,
        marker=None,
        bearer_factory=lambda: "Bearer x",
        sleep=fake_sleep,
    )
    mains = [s for s in samples if s.kind == "main"]
    dense = [s for s in samples if s.kind == "dense"]
    assert len(mains) == 6  # 3ticks(t=0, 2, 4)×2パス(各回で+50ms)
    assert len(dense) == 10  # 5ticks(t=0..4)×2パス
    assert all(s.ok for s in samples)
    assert all(s.tagged_planned_outage is False for s in samples)


async def test_marker_presence_tags_sample(tmp_path: Path):
    """マーカーファイルの存在がサンプルをタグ付けする(§2.1読み替え2)。"""
    clock = FakeClock(NOW)
    marker = tmp_path / "outage.marker"

    async def fake_sleep(seconds: float) -> None:
        clock.advance(timedelta(seconds=seconds))

    async def fake_request(path: str, bearer: str):
        pass

    def flip_marker():
        marker.write_text("1")

    samples = await run_probe(
        http=None,
        base_url="http://test",
        token_cache=None,
        duration_s=2.0,
        clock=clock,
        main_interval_s=1.0,
        dense_interval_s=1.0,
        request_path=fake_request,
        marker=marker,
        bearer_factory=lambda: "Bearer x",
        on_tick=flip_marker,
        sleep=fake_sleep,
    )
    assert any(s.tagged_planned_outage for s in samples)
    assert not all(s.tagged_planned_outage for s in samples)


def test_token_cache_reissues_after_50min():
    """50分経過で再発行する(access token TTL 1時間>窓60分対策)。"""
    from latch.loadgen.probe import TOKEN_REISSUE_SEC, TokenCache

    assert TOKEN_REISSUE_SEC == 50 * 60
    clock = FakeClock(NOW)
    issued: list[int] = []

    class _StubCache(TokenCache):
        async def _refresh(self):
            issued.append(1)
            self._issued_at = self._clock.now()
            self._token = f"tok-{len(issued)}"

    cache = _StubCache(http=None, keys_dir=None, subject="load-u0001", clock=clock)

    async def main():
        await cache.bearer()
        await cache.bearer()  # 経過0分: 再発行しない
        clock.advance(timedelta(minutes=11))  # 合計11分
        await cache.bearer()  # まだ11分: 再発行しない
        clock.advance(timedelta(minutes=40))  # 合計51分
        await cache.bearer()  # 再発行

    asyncio.run(main())
    # 再発行は2回(初期+51分時)。スタブは毎回リテラル1をappendするため
    # [1, 1]が2回の証拠(計画テストコードの欠陥修正: [1,1,2]は
    # literal-appendのスタブから出ない・2026-10-05)
    assert issued == [1, 1]
    assert cache._token == "tok-2"
