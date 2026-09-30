"""イベント基盤の運用CLI(ws-8 design §2.7)。

python -m latch.events purge-match-sub — 常設match-events subscriptionを削除
する。make test-ci のpytest終了後・worker復帰前に実行し、テスト中にAPIが
publishしたEventの常設subscriptionへの滞留(未配信メッセージ)を構造的に
全廃する(Pub/Subはsubscription削除で未配信メッセージごと消える)。
worker復帰(run)時の await bus.ensure() がcreate_subscription冪等で再作成
するため、workerは次の新規メッセージから処理を始める。存在しない
(NotFound)は「既に滞留なし」と同じ結果のため成功扱い(exit 0)。
"""

from __future__ import annotations

import argparse
import asyncio

from latch.events.pubsub_bus import PubsubEventBus
from latch.settings import Settings


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="latch.events",
        description="イベント基盤の運用CLI(ws-8)",
    )
    sub = parser.add_subparsers(dest="command", required=True)
    sub.add_parser(
        "purge-match-sub",
        help="常設match-events subscriptionを削除(滞留メッセージ全廃・"
        "worker復帰時にensure()が再作成)",
    )
    return parser


async def _run_purge(settings: Settings) -> int:
    bus = PubsubEventBus(settings)
    try:
        bus.delete_subscription()  # NotFoundは握る(pubsub_busと同一契約)
    finally:
        await bus.close()
    print(
        "[events] purged subscription:"
        f" {settings.pubsub_project_id}/"
        f"{settings.pubsub_subscription_match_events}"
    )
    return 0


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    if args.command == "purge-match-sub":
        return asyncio.run(_run_purge(Settings()))
    raise AssertionError(f"unreachable: {args.command}")


if __name__ == "__main__":
    raise SystemExit(main())
