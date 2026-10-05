"""staging非機能試験の運用CLI(M4 ws-7・design §3.1)。

latch-nfrtools d16 get daily           # 現在のJST日付キーの値を表示
latch-nfrtools d16 set daily 23999     # 日次カウンタを上書き
latch-nfrtools d16 delete monthly      # 月次キーを削除

宛先RedisはLATCH_REDIS_URL(Settings・env上書き可。Makefileのstaging-d16が
staging宛16379を設定)。キー導出はControlledClock(Redis基準の共有仮想時刻)
経由 — workerが見るClockと同一基準のため・stagingのClock操作後のset/getが
workerのキーと一致する(clock_cliと同一作法)。
"""

from __future__ import annotations

import argparse

from latch.core.clock import ControlledClock
from latch.nfrtools.d16 import (
    daily_key,
    delete_counter,
    get_counter,
    monthly_key,
    set_counter,
)
from latch.settings import Settings


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="latch-nfrtools",
        description="staging非機能試験の運用CLI(M4 ws-7)",
    )
    sub = parser.add_subparsers(dest="command", required=True)
    d16 = sub.add_parser("d16", help="D-16カウンタ操作(jev:daily/jev:monthly)")
    op = d16.add_subparsers(dest="operation", required=True)
    p_get = op.add_parser("get", help="値を表示(不在はNone)")
    p_get.add_argument("scope", choices=["daily", "monthly"])
    p_set = op.add_parser("set", help="値を上書き+TTL再設定")
    p_set.add_argument("scope", choices=["daily", "monthly"])
    # valueはstrのまま受けrun_set内でint変換する(TEC-001参照。type=intを
    # 付けるとargs.valueがintになりCLI層の契約とずれる)
    p_set.add_argument("value")
    p_del = op.add_parser("delete", help="キーを削除")
    p_del.add_argument("scope", choices=["daily", "monthly"])
    return parser


def _build():
    """Settings.redis_url宛の同期起点・非同期RedisとControlledClock。"""
    import redis.asyncio as aioredis

    settings = Settings()
    client = aioredis.Redis.from_url(settings.redis_url, decode_responses=True)
    import redis as redis_sync

    sync_client = redis_sync.Redis.from_url(settings.redis_url, decode_responses=True)
    return client, ControlledClock(sync_client)


async def run_get(redis, clock, scope: str) -> int:
    value = await get_counter(redis, clock, scope)
    key = f"{scope}:{clock.jst_date().strftime('%Y%m%d')}"
    print(f"[d16] {key} = {value}")
    return 0


async def run_set(redis, clock, scope: str, value: str) -> int:
    key = daily_key(clock) if scope == "daily" else monthly_key(clock)
    await set_counter(redis, clock, scope, int(value))
    print(f"[d16] set {key} = {value}")
    return 0


async def run_delete(redis, clock, scope: str) -> int:
    n = await delete_counter(redis, clock, scope)
    print(f"[d16] deleted {scope} ({n} key)")
    return 0


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    redis, clock = _build()

    async def dispatch():
        try:
            if args.operation == "get":
                return await run_get(redis, clock, args.scope)
            if args.operation == "set":
                return await run_set(redis, clock, args.scope, args.value)
            return await run_delete(redis, clock, args.scope)
        finally:
            await redis.aclose()

    import asyncio

    return asyncio.run(dispatch())


if __name__ == "__main__":
    raise SystemExit(main())
