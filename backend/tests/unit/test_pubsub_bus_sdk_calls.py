"""PubsubEventBusのSDK呼び出し形式のunit試験(スーパーバイザー検証検出欠陥の回帰ピン)。

実SDK経路(エミュレータ)はintegration(test_events_pipeline.py)で検証するため、
ここではSDKクライアントを記録スタブへ差し替え、gapic形式で呼んでいることを
呼び出し引数の形式でassertする。gapicクライアント(create_topic等)は第1位置
引数をrequestと解釈し、素の文字列は protobuf コンストラクタでTypeErrorに
なる(google-cloud-pubsub 2.41.0で検出)。publish/subscribeはクライアント
独自メソッドで位置引数が正式形式のため対象外。
"""

import os

import pytest
from google.api_core.exceptions import AlreadyExists

from latch.events.pubsub_bus import PubsubEventBus
from latch.settings import Settings

TOPIC_PATH = "projects/latch-ci/topics/match-events"
SUB_PATH = "projects/latch-ci/subscriptions/match-events-sub"


class RecordingPublisher:
    """create_topicの呼び出し(args/kwargs)を記録するスタブ。"""

    def __init__(self):
        self.calls: list[tuple[str, tuple, dict]] = []

    def create_topic(self, *args, **kwargs):
        self.calls.append(("create_topic", args, kwargs))
        raise AlreadyExists("topic exists")

    def topic_path(self, project, topic):
        return f"projects/{project}/topics/{topic}"


class RecordingSubscriber:
    """create_subscription/delete_subscriptionの呼び出しを記録するスタブ。"""

    def __init__(self):
        self.calls: list[tuple[str, tuple, dict]] = []

    def create_subscription(self, *args, **kwargs):
        self.calls.append(("create_subscription", args, kwargs))
        raise AlreadyExists("subscription exists")

    def delete_subscription(self, *args, **kwargs):
        self.calls.append(("delete_subscription", args, kwargs))

    def subscription_path(self, project, sub):
        return f"projects/{project}/subscriptions/{sub}"


@pytest.fixture
def bus_with_stubs():
    # PUBSUB_EMULATOR_HOST設定でクライアント生成が認証をバイパスする
    # (生成のみでRPCしない。ensure/deleteはスタブ差し替え後に呼ぶ)
    old = os.environ.get("PUBSUB_EMULATOR_HOST")
    os.environ["PUBSUB_EMULATOR_HOST"] = "127.0.0.1:8085"
    try:
        bus = PubsubEventBus(Settings())
    finally:
        if old is None:
            os.environ.pop("PUBSUB_EMULATOR_HOST", None)
        else:
            os.environ["PUBSUB_EMULATOR_HOST"] = old
    publisher, subscriber = RecordingPublisher(), RecordingSubscriber()
    bus._publisher = publisher
    bus._subscriber = subscriber
    return bus, publisher, subscriber


async def test_ensure_create_topic_uses_request_dict(bus_with_stubs):
    """create_topicはrequest辞書形式。位置引数の素文字列はrequestと解釈され
    TypeErrorになる(検出欠陥の回帰ピン)。AlreadyExistsは握られる。"""
    bus, publisher, _ = bus_with_stubs
    await bus.ensure()  # AlreadyExistsが握られて正常終了する
    name, args, kwargs = publisher.calls[0]
    assert name == "create_topic"
    assert args == ()  # 位置引数に素文字列を渡さない
    assert kwargs["request"] == {"name": TOPIC_PATH}


async def test_ensure_create_subscription_uses_keyword_form(bus_with_stubs):
    """create_subscriptionはkw-only(gapic正式形式)。位置引数なし。"""
    bus, _, subscriber = bus_with_stubs
    await bus.ensure()
    name, args, kwargs = subscriber.calls[0]
    assert name == "create_subscription"
    assert args == ()
    assert kwargs["name"] == SUB_PATH
    assert kwargs["topic"] == TOPIC_PATH
    assert kwargs["ack_deadline_seconds"] == 600


def test_delete_subscription_uses_request_dict(bus_with_stubs):
    """delete_subscriptionもrequest辞書形式。位置引数の素文字列は
    Subscription(request)と解釈されTypeErrorになる。"""
    bus, _, subscriber = bus_with_stubs
    bus.delete_subscription()
    name, args, kwargs = subscriber.calls[0]
    assert name == "delete_subscription"
    assert args == ()
    assert kwargs["request"] == {"subscription": SUB_PATH}
