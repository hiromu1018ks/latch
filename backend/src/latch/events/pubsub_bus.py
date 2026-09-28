"""google-cloud-pubsub実装のEventBus(04 §3選定。ci=エミュレータ — design §2.1)。

SDKは環境変数 PUBSUB_EMULATOR_HOST が設定されていると自動でエミュレータへ
接続する(公式の切替経路。本番=実GCPは settings.pubsub_emulator_host が空)。
隔離の実体はDB側(match_events.status)のためdead letter topic・subscription
宣言的retryには依らない(design §2.7)。
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import uuid
from collections.abc import Awaitable, Callable

from latch.events.bus import EventBus, IncomingEvent, Subscription
from latch.settings import Settings

logger = logging.getLogger(__name__)


class PubsubEventBus(EventBus):
    """API・Workerの両プロセスから使う(トピック・subscriptionは共有)。"""

    def __init__(self, settings: Settings, *, subscription: str | None = None) -> None:
        from google.cloud import pubsub_v1  # 遅延import(unit収集の軽量化)

        self._settings = settings
        if settings.pubsub_emulator_host:
            # SDK公式のエミュレータ切替(認証もバイパスされる)
            os.environ["PUBSUB_EMULATOR_HOST"] = settings.pubsub_emulator_host
        self._publisher = pubsub_v1.PublisherClient()
        self._subscriber = pubsub_v1.SubscriberClient()
        self._topic_path = self._publisher.topic_path(
            settings.pubsub_project_id, settings.pubsub_topic_match_events
        )
        self._subscription_path = self._subscriber.subscription_path(
            settings.pubsub_project_id,
            subscription or settings.pubsub_subscription_match_events,
        )

    async def ensure(self) -> None:
        """topic/subscriptionを作成(冪等)。AlreadyExistsは握る。"""
        from google.api_core.exceptions import AlreadyExists

        def _ensure() -> None:
            try:
                # gapic形式: 位置引数はrequestと解釈され、素の文字列は
                # Topic(request)のコンストラクタでTypeErrorになる
                # (google-cloud-pubsub 2.41.0・スーパーバイザー検証で検出)
                self._publisher.create_topic(request={"name": self._topic_path})
                logger.info("pubsub topic created: %s", self._topic_path)
            except AlreadyExists:
                pass
            try:
                self._subscriber.create_subscription(
                    name=self._subscription_path,
                    topic=self._topic_path,
                    ack_deadline_seconds=self._settings.pubsub_ack_deadline_sec,
                )
                logger.info("pubsub subscription created: %s", self._subscription_path)
            except AlreadyExists:
                pass

        await asyncio.to_thread(_ensure)

    async def publish_match_event(
        self, *, event_type: str, intent_id: uuid.UUID, version: int
    ) -> None:
        """3点組JSON({event_type, source_intent_id, version})をpublishする。"""
        data = json.dumps(
            {
                "event_type": event_type,
                "source_intent_id": str(intent_id),
                "version": version,
            }
        ).encode()
        await self.publish_raw(data)

    async def publish_raw(self, data: bytes) -> None:
        """生bytesをpublish(テストパブリッシャーの毒ペイロード投入用 — 10 §1)。"""
        future = self._publisher.publish(self._topic_path, data)
        await asyncio.wrap_future(future)

    async def subscribe(
        self, on_message: Callable[[IncomingEvent], Awaitable[None]]
    ) -> Subscription:
        """バックグラウンドスレッドのストリーミングpull(asyncio公式パターン)。

        callbackはgRPCスレッドで呼ばれるため、asyncioループへ
        run_coroutine_threadsafe で渡す。ackはSDKのmessage.ackをそのまま
        呼ぶ(スレッドセーフ)。
        """
        loop = asyncio.get_running_loop()

        def _callback(message) -> None:
            event = IncomingEvent.from_payload(
                message_id=message.message_id,
                payload=message.data,
                ack=message.ack,
            )
            asyncio.run_coroutine_threadsafe(on_message(event), loop)

        future = self._subscriber.subscribe(self._subscription_path, callback=_callback)
        return _PubsubSubscription(future)

    async def close(self) -> None:
        def _close() -> None:
            try:
                self._publisher.api_client.close()
            except Exception:  # noqa: BLE001 — 後始末の失敗は握る
                pass
            try:
                self._subscriber.close()
            except Exception:  # noqa: BLE001
                pass

        await asyncio.to_thread(_close)

    def delete_subscription(self) -> None:
        """試験専用subscriptionの削除(integration試験のteardown専用)。"""
        from google.api_core.exceptions import NotFound

        try:
            # gapic形式(create_topicと同じ理由でrequest辞書。位置引数の素文字列は
            # Subscription(request)のコンストラクタでTypeErrorになる)
            self._subscriber.delete_subscription(
                request={"subscription": self._subscription_path}
            )
        except NotFound:
            pass


class _PubsubSubscription(Subscription):
    def __init__(self, future) -> None:
        self._future = future

    def stop(self) -> None:
        # streaming pullのcancel(未ackメッセージは再配信 — at-least-once)
        self._future.cancel()


def make_event_bus(settings: Settings) -> EventBus:
    """プロセス構築用の工場(main.py・worker/main.pyが使用)。"""
    return PubsubEventBus(settings)
