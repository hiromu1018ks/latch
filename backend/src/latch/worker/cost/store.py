"""Redis上のJevコスト保護カウンタ(design §2.4)。

鍵(接頭辞 jev: — rl:/auth: と名前空間を分ける。ratelimit/store.pyのコメント
「JevカウンタはM2で別接頭辞」どおり):
  日次:   INCR jev:daily:{yyyymmdd}                TTL 48時間
  月次:   INCR jev:monthly:{yyyymm}                 TTL 45日
  Intent: INCR jev:intent:{intent_id}:{yyyymmdd}    TTL 48時間
  ユーザ: INCR jev:user:{user_id}:{yyyymmdd}        TTL 48時間
  内訳:   INCR jev:exec:{yyyymmdd}:{provider}       TTL 48時間
  再評価: SET reeval:{intent_id} "1" NX EX 1800(06 §5のキー名そのまま)

INCRとEXPIREはpipelineで毎回併発(ratelimitと同一規律)。TTLは掃除用に留め、
リセット表現には使わない(04 §5 — リセット=日付キー切替。JST 0時を跨ぐと
新キーで0から始まる=自己修復を内包 — design §2.5)。バケット文字列
(yyyymmdd等)は呼び出し側(guard)がClockから導出する(C2)。key_prefixは
integration試験での共用Redis干渉防止用(既定"")。
"""

from __future__ import annotations

import redis.asyncio as aioredis

_TTL_DAILY_S = 48 * 3600
_TTL_MONTHLY_S = 45 * 24 * 3600
_REEVAL_TTL_S = 1800  # 06 §5: 再評価頻度30分
_REPORT_TOP_N = 10  # alert添付レポートの上位件数(実装定義 — §9-6)


class JevCostStore:
    """Redis操作を閉じ込めるStore(decode_responses=True のRedisを注入)。"""

    def __init__(self, redis: aioredis.Redis, *, key_prefix: str = "") -> None:
        self._redis = redis
        self._prefix = key_prefix

    async def _incr(self, key: str, ttl_s: int) -> int:
        pipe = self._redis.pipeline()
        pipe.incr(key)
        pipe.expire(key, ttl_s)
        result = await pipe.execute()
        return int(result[0])

    async def incr_daily(self, day: str) -> int:
        """D-16 日次グローバルカウンタ(30,000)。"""
        return await self._incr(f"{self._prefix}jev:daily:{day}", _TTL_DAILY_S)

    async def incr_monthly(self, month: str) -> int:
        """D-16 月次グローバルカウンタ(600,000)。"""
        return await self._incr(f"{self._prefix}jev:monthly:{month}", _TTL_MONTHLY_S)

    async def incr_intent(self, intent_id: str, day: str) -> int:
        """1Intent 40回/日(06 §5)。"""
        return await self._incr(
            f"{self._prefix}jev:intent:{intent_id}:{day}", _TTL_DAILY_S
        )

    async def incr_user(self, user_id: str, day: str) -> int:
        """1ユーザー120回/日(06 §5・Active上限5×40の共有プール)。"""
        return await self._incr(f"{self._prefix}jev:user:{user_id}:{day}", _TTL_DAILY_S)

    async def record_execution(self, provider: str, day: str) -> int:
        """第一候補/フォールバック内訳(D-16「実際のAPI呼び出しを計上」)。

        実行経路確定後に+1する(ws-5が呼ぶ — design §2.4-3)。
        """
        return await self._incr(
            f"{self._prefix}jev:exec:{day}:{provider}", _TTL_DAILY_S
        )

    async def set_reeval_nx(self, intent_id: str) -> bool:
        """reeval:{intent_id} の SET NX EX 1800(06 §5・design §2.6)。

        True=確保成功(評価してよい)・False=30分以内の再評価。
        """
        return bool(
            await self._redis.set(
                f"{self._prefix}reeval:{intent_id}", "1", nx=True, ex=_REEVAL_TTL_S
            )
        )

    async def scan_report(self, day: str) -> dict:
        """80% alert添付の消費レポート(design §2.4-6・引用#7)。

        Intent別・ユーザー別の消費上位リストと経路別内訳。alertは80%跨ぎ時
        のみ(1日/月に高々数回)のためSCANのコストは許容する(design §2.4-6)。
        """
        intent_counts = await self._scan_counts(f"jev:intent:*:{day}")
        user_counts = await self._scan_counts(f"jev:user:*:{day}")
        exec_counts = await self._scan_counts(f"jev:exec:{day}:*")
        return {
            "intent_top": self._top(intent_counts),
            "user_top": self._top(user_counts),
            "exec_breakdown": dict(sorted(exec_counts.items())),
        }

    async def _scan_counts(self, pattern: str) -> dict[str, int]:
        counts: dict[str, int] = {}
        cursor = 0
        while True:
            cursor, keys = await self._redis.scan(
                cursor=cursor, match=f"{self._prefix}{pattern}", count=100
            )
            for key in keys:
                value = await self._redis.get(key)
                if value is not None:
                    counts[key] = int(value)
            if cursor == 0:
                break
        return counts

    @staticmethod
    def _top(counts: dict[str, int]) -> list[tuple[str, int]]:
        return sorted(counts.items(), key=lambda kv: (-kv[1], kv[0]))[:_REPORT_TOP_N]
