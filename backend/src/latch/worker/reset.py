"""リセットジョブ(M3-4・04 §5・design §2.5〜2.6)。

日次カウンタ(JST 0時)・月次カウンタ(暦月初JST 0時)の**同一ジョブ**での
リセット(FR-50)。機能的なリセット(日次30,000/月次600,000の上限復帰)は
日付キー切替で0時を跨いだ瞬間にINCRが成立させる(自己修復・引用#18)。
本ジョブの実体は「リセットの実行」の儀礼として旧キーを即時解放する
(TTL 48h/45日を待たない。月次キーの45日残留回避の意味が最も大きい)と、
完了時に保留キュー再評価として latch_engine.drain() を1回実行すること
(引用#11。イベントは発行しない — design §2.6・supervisor承認①)。
起動時の遡及はしない(カウンタは自己修復済み・drainは評価経路が代替)。
"""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Awaitable, Callable
from datetime import UTC, date, datetime, timedelta

from latch.core.clock import JST, Clock

logger = logging.getLogger(__name__)


def next_jst_midnight(now: datetime) -> datetime:
    """now(tz-aware UTC)より後の直近のJST 0時をUTC表現で返す(design §2.5)。

    nowがJST 0時丁度のときは翌日0時(待機0秒を生まない)。
    月末・年跨ぎはdateの+1日演算が処理する。
    """
    jst_now = now.astimezone(JST)
    next_date = jst_now.date() + timedelta(days=1)
    return datetime(
        next_date.year, next_date.month, next_date.day, 0, 0, 0, tzinfo=JST
    ).astimezone(UTC)


def _prev_month_key(today: date) -> str:
    """月初0時の掃除対象=前月の月次キー(yyyymm)。"""
    year = today.year - (1 if today.month == 1 else 0)
    month = 12 if today.month == 1 else today.month - 1
    return f"{year:04d}{month:02d}"


class ResetJob:
    """次のJST 0時まで待機→run_once(掃除+drain)。60秒系とは別周期の独立task。"""

    def __init__(
        self,
        *,
        cost_store,  # JevCostStore(掃除)
        clock: Clock,
        latch,  # LatchEngine(drain呼び出し・design §2.6)
        retry_sec: int = 300,
        sleep: Callable[[float], Awaitable[None]] = asyncio.sleep,
    ) -> None:
        self._cost_store = cost_store
        self._clock = clock
        self._latch = latch
        self._retry_sec = retry_sec
        self._sleep = sleep

    async def run_once(self) -> None:
        """0時発火の本体: 前日キー掃除(月初は前月月次)+drain(design §2.5)。

        DELは冪等(キー不在=0削除)。失敗した場合は例外を握らずrun()へ
        伝播する(runがretry_secで再試行 — 翌0時まで放置しない)。
        """
        jst_today = self._clock.jst_date()
        prev_day = (jst_today - timedelta(days=1)).strftime("%Y%m%d")
        await self._cost_store.delete_daily(prev_day)
        await self._cost_store.scan_delete(f"jev:exec:{prev_day}:*")
        await self._cost_store.scan_delete(f"jev:intent:*:{prev_day}")
        await self._cost_store.scan_delete(f"jev:user:*:{prev_day}")
        if jst_today.day == 1:
            await self._cost_store.delete_monthly(_prev_month_key(jst_today))
        await self._latch.drain()  # 完了時に保留キュー再評価(引用#11・§2.6)

    async def run(self, *, stop: asyncio.Event | None = None) -> None:
        """次のJST 0時まで待機→run_once(失敗時はretry_secで再試行)。

        待機中のstopで発火を挟まず終了(graceful shutdown・BackfillRunner
        と同一契約)。0時丁度でなく数秒遅れの発火を許容する(run_onceが
        Clock.jst_date()を再取得するため・design §2.5)。
        """
        while stop is None or not stop.is_set():
            now = self._clock.now()
            await self._wait((next_jst_midnight(now) - now).total_seconds(), stop)
            if stop is not None and stop.is_set():
                break
            while True:  # 失敗時はretry_secで再試行(翌0時まで放置しない)
                try:
                    await self.run_once()
                    break
                except Exception:
                    logger.warning("reset run_once failed", exc_info=True)
                    await self._wait(self._retry_sec, stop)
                    if stop is not None and stop.is_set():
                        return

    async def _wait(self, seconds: float, stop: asyncio.Event | None) -> None:
        """待機。注入sleepとstop待ちを並行させ、先に完了した方で返る。

        stopが来れば待機秒の残りを無視して即返る(shutdown応答性)。
        注入sleep(run(stop=event)でも使う)でunit試験が決定的に回せる。
        """
        if stop is None:
            await self._sleep(seconds)
            return
        sleep_task = asyncio.create_task(self._sleep(seconds))
        stop_task = asyncio.create_task(stop.wait())
        done, pending = await asyncio.wait(
            {sleep_task, stop_task}, return_when=asyncio.FIRST_COMPLETED
        )
        for task in pending:
            task.cancel()
        for task in done:
            task.result()  # 例外があれば再送出(待機自体の失敗は握らない)
