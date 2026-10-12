"""D-02警報線・A/B有害シグナルの週次自動検知(O-06・09 §6 D-02・05 v0.8 規定6)。

kpi_daily(kpi.pyのKpiJobが書く)を材料に、直近の完了週(日曜終了)へ対して
次の条件を検査し構造化ログ(WARNING)で発報する。媒体はQueueLagAlertと
同型(04 §5の先例・D-16/lag alertと同じ「構造化ログで開始」の段階実体):

- kpi_yes_rate_low   : 完了週のYES率(日次yes_rateの分子/分母を週へ集約)
                       が10%未満(D-02警報線1)
- kpi_mute_rate_spike: 通知オフ率(週次mute_rate)が前週比2倍(D-02警報線2)
- kpi_report_spike   : 通報率(週次report_rate)が前週比2倍
                       (09 §5のA/B停止ルールの有害シグナル。通知オフ率の
                       急増はD-02警報線と共用・発報を見た運用者がCLIで停止)

発報は(週, kind)ごと1回: Redisアームキー(SET NX+TTL)でworker複数構成の
二重発報を防ぐ(lag_alertと同一規律・Redis故障はfail-openで単発化のみ劣化)。
検査の前提は完了週の週次行の存在(KpiJobが日曜を含むtickで書く
intent_recreation_rate代表行とmute_rate/report_rate行・日次yes_rate 7日分)。
不足なら検査しない(集計前・集計漏れの週で誤報しない)。

ノイズ抑止の設計判断(根拠はmatrix §11へ記録):
- yes率: 週の回答数(分母)がmin_den(既定10)未満は判定しない
- 急増: 前週の分子が0の週は「0→n」を急増としない(倍率が定義できない)
- 発見的事実と閾値はイベントpayloadに含め、人間の判断材料に出す

判定の実施(発報を見て閾値・D-08上限の見直しを起動するか)はPhase 2以降の
運用であり、本モジュールは検知と発報のみを担う(09 §6 D-02)。
"""

from __future__ import annotations

import asyncio
import json
import logging
from collections.abc import Awaitable, Callable
from datetime import timedelta

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncEngine

from latch.core.clock import Clock
from latch.obs.kpi import week_of

logger = logging.getLogger("latch.kpi.alert")

ARM_KEY_PREFIX = "latch:alert:kpi"
YES_RATE_MIN = 0.10  # D-02: YES率10%未満
SPIKE_FACTOR = 2  # D-02: 週比2倍
MIN_DEN = 10  # ノイズ抑止の最小分母(設計判断)
ARM_TTL_S = 8 * 24 * 3600  # 週を超えて保持し翌週は再検知可能にする掃き忘れ防止

_SQL_WEEKLY_ROW = text("""
    SELECT numerator, denominator FROM kpi_daily
    WHERE metric_key = :metric AND day = CAST(:day AS date)
""")

_SQL_DAILY_YES = text("""
    SELECT numerator, denominator FROM kpi_daily
    WHERE metric_key = 'yes_rate' AND day = CAST(:day AS date)
""")


def structured_log_alert(event: dict) -> None:
    """alert媒体の段階実体(構造化ログ・QueueLagAlertと同型)。"""
    logger.warning("kpi alert %s", json.dumps(event, ensure_ascii=False))


def weekly_yes_totals(rows: list) -> tuple[int, int]:
    """日次yes_rate行(分子, 分母)の週合計(不足行は呼び出し側が検査済み)。"""
    num = sum(int(r[0]) for r in rows)
    den = sum(int(r[1]) for r in rows)
    return num, den


def yes_rate_low(num: int, den: int, *, min_rate: float, min_den: int) -> bool:
    """D-02警報線1(YES率<min_rate)。分母がmin_den未満は判定しない。"""
    if den < min_den:
        return False
    return (num / den) < min_rate


def rate_spike(
    curr_num: int,
    curr_den: int,
    prev_num: int,
    prev_den: int,
    *,
    factor: float,
) -> bool:
    """週比factor倍の急増判定(警報線2・A/B有害シグナル)。

    前週分子0・いずれかの分母0は判定しない(倍率が定義できない)。
    """
    if prev_num <= 0 or curr_den <= 0 or prev_den <= 0:
        return False
    return (curr_num / curr_den) >= factor * (prev_num / prev_den)


class KpiAlertJob:
    """周期タスク: check_onceをinterval秒ごとに繰り返す(KpiJobと同一契約)。"""

    def __init__(
        self,
        *,
        engine: AsyncEngine,
        clock: Clock,
        redis=None,
        yes_rate_min: float = YES_RATE_MIN,
        spike_factor: float = SPIKE_FACTOR,
        min_den: int = MIN_DEN,
        arm_ttl_s: int = ARM_TTL_S,
        interval_sec: int = 60,
        alert_emitter: Callable[[dict], None] = structured_log_alert,
        sleep: Callable[[float], Awaitable[None]] = asyncio.sleep,
    ) -> None:
        self._engine = engine
        self._clock = clock
        self._redis = redis
        self._yes_rate_min = yes_rate_min
        self._spike_factor = spike_factor
        self._min_den = min_den
        self._arm_ttl_s = arm_ttl_s
        self._interval_sec = interval_sec
        self._alert_emitter = alert_emitter
        self._sleep = sleep
        self._local_armed: set[str] = set()

    async def check_once(self) -> list[dict]:
        """直近完了週の検査1回分。発報したイベントのlist(検査不能・条件不成立は空)。"""
        today = self._clock.jst_date()
        yesterday = today - timedelta(days=1)
        # 直近の完了週 = 昨日以前で最も近い日曜が終わりの週(KpiJobの週次
        # 集計トリガーと同じ「日曜を含むtick」の翌日以降に検査可能になる)
        week_end = yesterday
        while week_end.weekday() != 6:
            week_end -= timedelta(days=1)
        week_start = week_of(week_end)
        events: list[dict] = []
        async with self._engine.connect() as conn:
            yes_rows = [
                (
                    await conn.execute(
                        _SQL_DAILY_YES, {"day": week_start + timedelta(days=i)}
                    )
                ).first()
                for i in range(7)
            ]
            if all(r is not None for r in yes_rows):
                num, den = weekly_yes_totals(yes_rows)
                if yes_rate_low(
                    num, den, min_rate=self._yes_rate_min, min_den=self._min_den
                ):
                    events.append(
                        {
                            "kind": "kpi_yes_rate_low",
                            "week_start": week_start.isoformat(),
                            "yes_rate": round(num / den, 4),
                            "threshold": self._yes_rate_min,
                            "yes": num,
                            "answers": den,
                        }
                    )
            for kind, metric in (
                ("kpi_mute_rate_spike", "mute_rate"),
                ("kpi_report_spike", "report_rate"),
            ):
                curr = (
                    await conn.execute(
                        _SQL_WEEKLY_ROW,
                        {"metric": metric, "day": week_start},
                    )
                ).first()
                prev = (
                    await conn.execute(
                        _SQL_WEEKLY_ROW,
                        {"metric": metric, "day": week_start - timedelta(days=7)},
                    )
                ).first()
                if curr is None or prev is None:
                    continue
                if rate_spike(
                    int(curr[0]),
                    int(curr[1]),
                    int(prev[0]),
                    int(prev[1]),
                    factor=self._spike_factor,
                ):
                    events.append(
                        {
                            "kind": kind,
                            "week_start": week_start.isoformat(),
                            "current": round(int(curr[0]) / int(curr[1]), 6),
                            "previous": round(int(prev[0]) / int(prev[1]), 6),
                            "factor": self._spike_factor,
                            "curr_num": int(curr[0]),
                            "curr_den": int(curr[1]),
                            "prev_num": int(prev[0]),
                            "prev_den": int(prev[1]),
                        }
                    )
        fired: list[dict] = []
        for event in events:
            key = f"{ARM_KEY_PREFIX}:{event['kind']}:{event['week_start']}"
            if await self._arm(key):
                self._alert_emitter(event)
                fired.append(event)
        return fired

    async def _arm(self, key: str) -> bool:
        """(週, kind)単位の単発化。Redis不在はプロセス内集合で代用。"""
        if self._redis is None:
            if key in self._local_armed:
                return False
            self._local_armed.add(key)
            return True
        try:
            return bool(await self._redis.set(key, "1", nx=True, ex=self._arm_ttl_s))
        except Exception:
            logger.warning("kpi alert arm failed", exc_info=True)
            return True

    async def run(self, stop: asyncio.Event | None = None) -> None:
        """起動時即check→interval待ち→check→…。例外は握ってwarning+次tick
        (KpiJob.runと同一契約 — 警報の失敗でworkerを落とさない)。"""
        while stop is None or not stop.is_set():
            try:
                await self.check_once()
            except Exception:
                logger.warning("kpi alert check failed", exc_info=True)
            await self._wait_interval(stop)

    async def _wait_interval(self, stop: asyncio.Event | None) -> None:
        if stop is None:
            await self._sleep(self._interval_sec)
            return
        sleep_task = asyncio.create_task(self._sleep(self._interval_sec))
        stop_task = asyncio.create_task(stop.wait())
        done, pending = await asyncio.wait(
            {sleep_task, stop_task}, return_when=asyncio.FIRST_COMPLETED
        )
        for task in pending:
            task.cancel()
        for task in done:
            task.result()
