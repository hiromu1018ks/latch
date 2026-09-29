"""Jev実行のコスト保護判定(04 §4 D-16・06 §5・design §2.4)。

INCR先行: 4カウンタ(intent別40/日・user別120/日・日次30,000・月次600,000)を
INCRしてから上限と比較する。denyされた要求もカウントを消費する(判定と
カウントの間の並行すり抜けをRedis上で潰す — 引用#8「超過幅を1要求分」)。
80% alertと月次上限到達ログはINCR戻り値が丁度しきい値に等しいとき1回だけ
発報する(prev < threshold ≤ curr の跨ぎ検出。Redis INCRの原子性により跨ぎは
1回 — design §2.4-4)。alert媒体は構造化ログで開始(媒体差し替え可能な
emitter注入 — supervisor承認 2026-09-29)。Redis例外は専用例外でfail-closed
(ratelimitのRateLimitDependencyErrorと同型)。バケット文字列はClockから導出
(C2・引用#15)。
"""

from __future__ import annotations

import json
import logging
import uuid
from collections.abc import Callable
from dataclasses import dataclass

from latch.core.clock import Clock
from latch.worker.cost.errors import JevCostDependencyError
from latch.worker.cost.store import JevCostStore

logger = logging.getLogger(__name__)

# D-16(04 §4)・06 §5 の確定値(§9固定値・settings化しない — design §2.8-4)
DAILY_LIMIT = 30_000
DAILY_ALERT = 24_000  # 80%
MONTHLY_LIMIT = 600_000
MONTHLY_ALERT = 480_000  # 80%
INTENT_DAILY_LIMIT = 40
USER_DAILY_LIMIT = 120

# deny理由(JevDecision.deny_reason の値域・design §2.4-2の列挙順が優先順位)
DENY_INTENT_DAILY = "intent_daily"
DENY_USER_DAILY = "user_daily"
DENY_GLOBAL_DAILY = "global_daily"
DENY_GLOBAL_MONTHLY = "global_monthly"


def structured_log_alert(event: dict) -> None:
    """alert媒体のci段階実体(構造化ログ — supervisor承認・design §2.4-5)。

    宛先・エスカレーションは運用設計に委ねられているため(引用#9)、媒体は
    この関数の差し替えで切り替える。
    """
    logging.getLogger("latch.cost.alert").warning(
        "jev cost alert %s", json.dumps(event, ensure_ascii=False)
    )


@dataclass(frozen=True)
class JevDecision:
    """request_execution の結果(deny時は理由付き — 縮退記録はws-5)。"""

    allowed: bool
    deny_reason: str | None = None


class JevCostGuard:
    """Jev実行の直前に呼ぶ判定(design §2.4-1。呼び出し元はws-5)。"""

    def __init__(
        self,
        *,
        store: JevCostStore,
        clock: Clock,
        alert_emitter: Callable[[dict], None] = structured_log_alert,
    ) -> None:
        self._store = store
        self._clock = clock
        self._alert_emitter = alert_emitter

    def _day_bucket(self) -> str:
        return self._clock.jst_date().strftime("%Y%m%d")

    def _month_bucket(self) -> str:
        return self._clock.jst_date().strftime("%Y%m")

    def _next_month_start_jst(self) -> str:
        """復帰予定=暦月初のJST 0時(D-16・design §2.4-4)。"""
        today = self._clock.jst_date()
        year = today.year + (1 if today.month == 12 else 0)
        month = 1 if today.month == 12 else today.month + 1
        return f"{year:04d}-{month:02d}-01T00:00:00+09:00"

    async def request_execution(
        self, intent_id: uuid.UUID, user_id: uuid.UUID
    ) -> JevDecision:
        """4カウンタINCR先行判定(design §2.4-2)。denyもカウントを消費する。"""
        day = self._day_bucket()
        month = self._month_bucket()
        try:
            intent_count = await self._store.incr_intent(str(intent_id), day)
            user_count = await self._store.incr_user(str(user_id), day)
            daily_count = await self._store.incr_daily(day)
            monthly_count = await self._store.incr_monthly(month)
        except Exception as exc:
            raise JevCostDependencyError("jev cost store unavailable") from exc
        if daily_count == DAILY_ALERT:
            await self._emit(
                {"kind": "daily_80pct", "day": day, "count": daily_count}, day
            )
        if monthly_count == MONTHLY_ALERT:
            await self._emit(
                {"kind": "monthly_80pct", "month": month, "count": monthly_count},
                day,
            )
        if monthly_count == MONTHLY_LIMIT + 1:
            await self._emit(
                {
                    "kind": "monthly_limit",
                    "month": month,
                    "count": monthly_count,
                    "resume_at": self._next_month_start_jst(),
                },
                day,
            )
        if intent_count > INTENT_DAILY_LIMIT:
            return JevDecision(allowed=False, deny_reason=DENY_INTENT_DAILY)
        if user_count > USER_DAILY_LIMIT:
            return JevDecision(allowed=False, deny_reason=DENY_USER_DAILY)
        if daily_count > DAILY_LIMIT:
            return JevDecision(allowed=False, deny_reason=DENY_GLOBAL_DAILY)
        if monthly_count > MONTHLY_LIMIT:
            return JevDecision(allowed=False, deny_reason=DENY_GLOBAL_MONTHLY)
        return JevDecision(allowed=True, deny_reason=None)

    async def _emit(self, event: dict, day: str) -> None:
        """alert発報(引用#7: レポート本文を添付)。scan失敗でalert自体は落とさない。"""
        try:
            event["report"] = await self._store.scan_report(day)
        except Exception:
            logger.warning("jev alert report scan failed", exc_info=True)
        self._alert_emitter(event)
