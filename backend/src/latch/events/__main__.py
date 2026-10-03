"""イベント基盤の運用CLI(ws-8 design §2.7・M4 ws-1 design §2.5)。

python -m latch.events purge-match-sub — 常設match-events subscriptionを削除
する。make test-ci のpytest終了後・worker復帰前に実行し、テスト中にAPIが
publishしたEventの常設subscriptionへの滞留(未配信メッセージ)を構造的に
全廃する(Pub/Subはsubscription削除で未配信メッセージごと消える)。
worker復帰(run)時の await bus.ensure() がcreate_subscription冪等で再作成
するため、workerは次の新規メッセージから処理を始める。存在しない
(NotFound)は「既に滞留なし」と同じ結果のため成功扱い(exit 0)。

python -m latch.events publish <event_type> --intent-id <uuid> [--version N]
[--raw-payload '<json>'] — Match Eventを手動発行する(10 §1「疑似イベント
注入」・M4 ws-1)。6種(created/updated/deleted/expired/scheduled/
embedding_completed)すべてを手動発火可(scheduledの常時発行経路は存在しない
ため、手動発火はこの種別を観察する唯一の経路)。発行は
EventBus.publish_match_eventの同一経路(3点組 — 発行形式の二重管理を
作らない)。--raw-payloadは生bytesを直接publishする(毒ペイロード・参照不在
Event試験用。3点組より優先)。宛先は LATCH_PUBSUB_EMULATOR_HOST /
LATCH_PUBSUB_PROJECT_ID のenv上書きでstaging/load宛へ向ける(purgeと同一方式)。
"""

from __future__ import annotations

import argparse
import asyncio
import uuid

from latch.events.pubsub_bus import PubsubEventBus
from latch.intents.events import (
    EVENT_CREATED,
    EVENT_DELETED,
    EVENT_EMBEDDING_COMPLETED,
    EVENT_EXPIRED,
    EVENT_SCHEDULED,
    EVENT_UPDATED,
)
from latch.settings import Settings

PUBLISH_EVENT_TYPES = (
    EVENT_CREATED,
    EVENT_UPDATED,
    EVENT_DELETED,
    EVENT_EXPIRED,
    EVENT_SCHEDULED,
    EVENT_EMBEDDING_COMPLETED,
)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="latch.events",
        description="イベント基盤の運用CLI(ws-8・M4 ws-1)",
    )
    sub = parser.add_subparsers(dest="command", required=True)
    sub.add_parser(
        "purge-match-sub",
        help="常設match-events subscriptionを削除(滞留メッセージ全廃・"
        "worker復帰時にensure()が再作成)",
    )
    publish = sub.add_parser(
        "publish",
        help="Match Eventを手動発行(6種。10 §1「疑似イベント注入」)",
    )
    publish.add_argument(
        "event_type",
        choices=list(PUBLISH_EVENT_TYPES),
        help="発行するEvent種別",
    )
    publish.add_argument(
        "--intent-id",
        required=True,
        type=uuid.UUID,
        help="対象IntentのUUID",
    )
    publish.add_argument("--version", type=int, default=1, help="3点組のversion(既定1)")
    publish.add_argument(
        "--raw-payload",
        default=None,
        help="生JSON文字列をそのままpublishする(毒ペイロード試験用。"
        "3点組の組立より優先)",
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


async def _run_publish(settings: Settings, args: argparse.Namespace) -> int:
    bus = PubsubEventBus(settings)
    try:
        if args.raw_payload is not None:
            # 毒ペイロード・参照不在Eventの投入経路(10 §4.7)。3点組より優先
            await bus.publish_raw(args.raw_payload.encode())
            print(
                "[events] published raw payload"
                f" ({len(args.raw_payload.encode())} bytes)"
            )
        else:
            await bus.publish_match_event(
                event_type=args.event_type,
                intent_id=args.intent_id,
                version=args.version,
            )
            print(
                f"[events] published {args.event_type}"
                f" intent={args.intent_id} version={args.version}"
            )
    finally:
        await bus.close()
    return 0


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    if args.command == "purge-match-sub":
        return asyncio.run(_run_purge(Settings()))
    if args.command == "publish":
        return asyncio.run(_run_publish(Settings(), args))
    raise AssertionError(f"unreachable: {args.command}")


if __name__ == "__main__":
    raise SystemExit(main())
