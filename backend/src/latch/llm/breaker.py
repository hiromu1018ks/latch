"""Circuit Breaker(06 §8 D-15 FR-10・ws-8 design §2.2・§2.1案A)。

第一候補TypeSafe Jev呼び出しの継続障害検知。測定窓1分で(a)エラー率50%超
または(b)p95レイテンシがtimeout相当以上の呼び出しで開放、開放から60秒後に
半開で1リクエストのみ試験。状態はWorkerプロセス内メモリ(再起動でclosedから
出直す — design §2.1)。複数Worker共有化はM4構成確定後にRedisへ移行
(IF不変・design §2.11-1)。

確定値(design §2.2・supervisor承認事項1・2):
- エラー率判定は窓内呼び出し数 N >= min_samples(=2) かつ error数/N > 0.5。
  N=1の失敗では開放しない(「単発のtimeoutやレート制限のスパイクでは発動
  せず」の実装解釈)
- p95判定は窓内呼び出しのレイテンシ昇順ソートで p95位置=ceil(0.95×N)−1
  (0-indexed)の値が latency_threshold_s 以上なら開放。timeout
  (asyncio.timeout打ち切り)呼び出しは打ち切り時点のtimeout_sをレイテンシ
  として記録するため母集団に入る(成功呼び出しは必ずtimeout_s未満)。
  min_samplesはエラー率判定のみに適用しp95判定には適用しない
- 時刻参照はClock注入のみ(arch test規律・C2)。パラメータはenvに出さない
  (Timeoutsと同様のコンストラクタ上書き式)
"""

from __future__ import annotations

import logging
import math
from collections import deque
from dataclasses import dataclass
from datetime import datetime, timedelta

from latch.core.clock import Clock

logger = logging.getLogger("latch.breaker")

WINDOW_S = 60.0
ERROR_RATE_THRESHOLD = 0.5
HALF_OPEN_AFTER_S = 60.0
MIN_SAMPLES = 2
LATENCY_THRESHOLD_S = 6.0  # 既定はTimeouts.jev_sと同値(07 §1・引用#2)

CLOSED = "closed"
OPEN = "open"
HALF_OPEN = "half_open"


@dataclass(frozen=True)
class BreakerParams:
    """測定窓・しきい値(design §2.2)。unit試験で短縮注入するための上書き経路。"""

    window_s: float = WINDOW_S
    error_rate_threshold: float = ERROR_RATE_THRESHOLD
    half_open_after_s: float = HALF_OPEN_AFTER_S
    min_samples: int = MIN_SAMPLES


class CircuitBreaker:
    """closed/open/half_openの3状態。allow→呼び出し→recordの契約。"""

    def __init__(
        self,
        *,
        clock: Clock,
        params: BreakerParams | None = None,
        latency_threshold_s: float = LATENCY_THRESHOLD_S,
    ) -> None:
        self._clock = clock
        self._params = params if params is not None else BreakerParams()
        # p95しきい値はGatewayがTimeouts.jev_sと同じ値を渡す(design §2.2)
        self._latency_threshold_s = latency_threshold_s
        self._state = CLOSED
        self._opened_at: datetime | None = None
        self._samples: deque[tuple[datetime, bool, float]] = deque()
        self._half_open_probe_pending = False

    @property
    def state(self) -> str:
        return self._state

    def samples(self) -> tuple[tuple[bool, float], ...]:
        """窓内サンプルの読み取り専用ビュー(unit試験・診断用。§9-8)。"""
        return tuple((error, latency) for _, error, latency in self._samples)

    def allow(self, now: datetime) -> bool:
        """第一候補呼び出しの可否。openは期限到達でhalf_openへ遷移する。"""
        if self._state == CLOSED:
            return True
        if self._state == OPEN:
            assert self._opened_at is not None
            elapsed = now - self._opened_at
            if elapsed >= timedelta(seconds=self._params.half_open_after_s):
                self._transition(HALF_OPEN, reason="half_open_after")
                self._half_open_probe_pending = True
                return True  # この呼び出しが半開の試験リクエスト
            return False
        # half_open: 前回の試験リクエストが未recordなら防御的に拒否
        # (WorkerはEvent直列処理のため通常発生しない — design §2.2)
        return not self._half_open_probe_pending

    def record(self, *, error: bool, latency_s: float) -> None:
        """呼び出し1回の記録。窓外サンプルを除去して追記し、遷移を判定。"""
        now = self._clock.now()
        if self._state == HALF_OPEN:
            # 試験リクエストの結果: 成功ならclosed+窓リセット・失敗ならopen戻し
            self._half_open_probe_pending = False
            if error:
                self._transition(OPEN, reason="half_open_probe_failed")
            else:
                self._transition(CLOSED, reason="half_open_probe_succeeded")
            self._opened_at = now if error else None
            self._samples.clear()
            return
        cutoff = now - timedelta(seconds=self._params.window_s)
        while self._samples and self._samples[0][0] < cutoff:
            self._samples.popleft()
        self._samples.append((now, error, latency_s))
        if self._state == CLOSED:
            reason = self._open_reason()
            if reason is not None:
                self._transition(OPEN, reason=reason)
                self._opened_at = now

    def _open_reason(self) -> str | None:
        """closed中のrecord直後の開放判定。どちらか先で開放(§9-3)。"""
        n = len(self._samples)
        if n >= self._params.min_samples:
            errors = sum(1 for _, error, _ in self._samples if error)
            if errors / n > self._params.error_rate_threshold:
                return "error_rate"
        latencies = sorted(latency for _, _, latency in self._samples)
        p95_index = math.ceil(0.95 * n) - 1
        if latencies[p95_index] >= self._latency_threshold_s:
            return "p95_latency"
        return None

    def _transition(self, new_state: str, *, reason: str) -> None:
        old = self._state
        self._state = new_state
        logger.info("breaker state %s -> %s reason=%s", old, new_state, reason)
