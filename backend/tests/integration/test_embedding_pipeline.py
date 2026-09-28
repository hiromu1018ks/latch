"""Embeddingパイプラインのci環境実証(M2 ws-2 design §4.2)。

実HTTP(compose api)・実DB・実Pub/Subエミュレータ(127.0.0.1:8085)。
GatewayはStubLLMラップの記録スタブ(呼び出し回数・渡されたtextを記録 —
design §4.2)。Worker処理はテストプロセス内Worker(bus=実エミュレータ・
clock=FakeClock・engine=実DB・embedding=記録スタブ注入)が担う。
**実行はapi/workerイメージ再ビルド後の make test-ci のみ**(スーパーバイザー
検証時)。試験専用subscriptionを作成/削除し試験間の残余メッセージ干渉を
構造的に排除する(ws-1のtest_events_pipeline.pyと同じ構成)。
"""

import asyncio
import os
import re
import sys
import uuid as uuid_mod
from datetime import timedelta

import pytest
from sqlalchemy import text

from latch.core.clock import FakeClock, SystemClock
from latch.llm.errors import LLMProviderError
from latch.llm.stub import StubLLM

pytestmark = pytest.mark.integration

# テストプロセスからはport映射経由(コンテナ内はpubsub:8085 — compose.yaml)
os.environ.setdefault("LATCH_PUBSUB_EMULATOR_HOST", "127.0.0.1:8085")

# env設定後にimport(PubsubEventBus構築がPUBSUB_EMULATOR_HOSTを読むため)
from latch.events import PubsubEventBus  # noqa: E402
from latch.settings import Settings  # noqa: E402
from latch.worker.backfill import BackfillRunner  # noqa: E402
from latch.worker.embedding import EmbeddingWorker  # noqa: E402
from latch.worker.main import Worker  # noqa: E402


async def _instant(_seconds: float) -> None:
    """バックオフ待機の即時化(ws-1と同じ規律)。"""
    return None


# -- 共通ヘルパ(test_events_pipeline.py と同じ流儀)--


async def _cli_idp_token(provider: str, subject: str) -> str:
    proc = await asyncio.create_subprocess_exec(
        sys.executable,
        "-m",
        "latch.auth",
        "issue-idp-token",
        "--provider",
        provider,
        "--subject",
        subject,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    stdout, stderr = await proc.communicate()
    assert proc.returncode == 0, stderr.decode()
    return stdout.decode().strip()


async def _register(api_client, subject: str, birth_date: str = "1990-04-01"):
    idp_token = await _cli_idp_token("google", subject)
    tok = await api_client.post(
        "/v1/auth/token", json={"provider": "google", "idp_token": idp_token}
    )
    assert tok.status_code == 200, tok.text
    headers = {"Authorization": f"Bearer {tok.json()['access_token']}"}
    created = await api_client.post(
        "/v1/users",
        headers=headers,
        json={"display_name": "m2ws2", "birth_date": birth_date, "profile": {}},
    )
    assert created.status_code == 201, created.text
    return headers, created.json()["user"]["id"]


def _future(hours: float) -> str:
    now = SystemClock().now()
    return (now + timedelta(hours=hours)).isoformat()


def _structured() -> dict:
    return {
        "category": {"primary": "drinking", "secondary": None},
        "alcohol_involved": False,
        "time": {"start": _future(3), "end": None},
        "location": {"name": "天文館"},
    }


def _active_payload() -> dict:
    return {
        "raw_text": "明日の夜 天文館で軽く飲みたい",
        "status": "active",
        "structured_intent": _structured(),
    }


async def _fetch_event(db_engine, event_type: str, intent_id, version: int):
    async with db_engine.connect() as conn:
        res = await conn.execute(
            text(
                "SELECT status, payload FROM match_events"
                " WHERE event_type = :et AND source_intent_id = :iid"
                " AND payload->>'version' = :v"
            ),
            {"et": event_type, "iid": intent_id, "v": str(version)},
        )
        return res.first()


async def _wait_status(
    db_engine, event_type: str, intent_id, version: int, timeout: float = 20.0
):
    """実時間ポーリング(テストコードは実時間参照可 — arch test対象外)。"""
    deadline = asyncio.get_event_loop().time() + timeout
    while asyncio.get_event_loop().time() < deadline:
        row = await _fetch_event(db_engine, event_type, intent_id, version)
        if row is not None and row[0] in ("processed", "quarantined"):
            return row
        await asyncio.sleep(0.2)
    pytest.fail(
        f"timeout: match_events({event_type}, {intent_id}, v{version}) not finalized"
    )


async def _fetch_embedding(db_engine, intent_id):
    async with db_engine.connect() as conn:
        res = await conn.execute(
            text(
                "SELECT embedding IS NOT NULL, embedding_model"
                " FROM intents WHERE id = :i"
            ),
            {"i": intent_id},
        )
        return res.first()


# -- 記録Gateway(design §4.2: 呼び出し回数・渡されたtextを記録)--


class _RecordingGateway:
    """EmbeddingWorker注入用Gatewayスタブ(失敗注入はfailフラグで実行時切替)。"""

    def __init__(self):
        self.calls: list[dict] = []
        self.fail = False
        self._stub = StubLLM()

    async def embed_intent(self, *, text: str, intent_id: str) -> list[float]:
        self.calls.append({"text": text, "intent_id": intent_id})
        if self.fail:
            raise LLMProviderError("recording gateway: injected failure")
        return await self._stub.embed(text)


# -- fixture: 試験専用subscription + テストプロセス内Worker --


class _WorkerEnv:
    def __init__(self, bus, clock, worker, task, gateway, embedding):
        self.bus = bus
        self.clock = clock
        self.worker = worker
        self.task = task
        self.gateway = gateway
        self.embedding = embedding


@pytest.fixture
async def worker_env(db_engine):
    sub_name = f"match-events-test-{uuid_mod.uuid4().hex[:8]}"
    settings = Settings()
    bus = PubsubEventBus(settings, subscription=sub_name)
    for _ in range(40):  # エミュレータ起動待ち(最大20秒)
        try:
            await bus.ensure()
            break
        except Exception:
            await asyncio.sleep(0.5)
    else:
        pytest.fail("pubsub emulator not reachable at 127.0.0.1:8085")
    clock = FakeClock(SystemClock().now())
    gateway = _RecordingGateway()
    embedding = EmbeddingWorker(engine=db_engine, clock=clock, gateway=gateway, bus=bus)
    worker = Worker(
        clock=clock,
        settings=settings,
        bus=bus,
        engine=db_engine,
        sleep=_instant,
        embedding=embedding,
    )
    task = asyncio.create_task(worker.run())
    await asyncio.sleep(0.1)  # subscribe開始を待つ
    try:
        yield _WorkerEnv(
            bus=bus,
            clock=clock,
            worker=worker,
            task=task,
            gateway=gateway,
            embedding=embedding,
        )
    finally:
        worker.request_shutdown()
        try:
            await asyncio.wait_for(task, timeout=5.0)
        except TimeoutError:
            task.cancel()
        # 残余メッセージを次試験へ残さない(closeより前に削除 — ws-1と同じ)
        bus.delete_subscription()
        await bus.close()


@pytest.fixture
async def user_env(api_client, db_engine):
    """subject実行ごと一意のuser。後始末でmatch_events・intent行を消す。"""
    subject = f"m2ws2-{uuid_mod.uuid4().hex[:12]}"
    headers, user_id = await _register(api_client, subject)
    st = {"headers": headers, "user_id": user_id}
    try:
        yield st
    finally:
        async with db_engine.begin() as conn:
            await conn.execute(
                text(
                    "DELETE FROM match_events WHERE source_intent_id IN"
                    " (SELECT id FROM intents WHERE user_id = :uid)"
                ),
                {"uid": user_id},
            )
            await conn.execute(
                text("DELETE FROM intents WHERE user_id = :uid"), {"uid": user_id}
            )


async def _create_active(api_client, headers) -> dict:
    resp = await api_client.post("/v1/intents", headers=headers, json=_active_payload())
    assert resp.status_code == 201, resp.text
    return resp.json()["intent"]  # IntentEnvelopeラップ(ws-1と同じ)


# -- §4.2の7試験 --

# 時間帯要素の形式(実行時刻相対の_timeで完全ピンできないため構造検証 — 07 §3)
_TIME_PART_RE = re.compile(
    r"^(平日|週末)(朝|昼|夕方|夜|深夜)\d+時以降$|^(平日|週末)(朝|昼|夕方|夜|深夜)\d+-\d+時$"
)


async def test_1_e2e_create_embeds_and_emits(
    api_client, db_engine, worker_env, user_env
):
    """E2E(作成): embedding NOT NULL・model記録・embedding_completed行processed。
    Gatewayへ渡されたtextが正規化形式でraw_textを含まない(実経路の#10)。"""
    intent = await _create_active(api_client, user_env["headers"])
    await _wait_status(db_engine, "created", intent["id"], intent["version"])
    await _wait_status(
        db_engine, "embedding_completed", intent["id"], intent["version"]
    )
    row = await _fetch_embedding(db_engine, intent["id"])
    assert row[0] is True
    assert row[1] == "gemini-embedding-001"  # 01 §18(モデル識別子+版の記録)
    assert len(worker_env.gateway.calls) == 1
    got = worker_env.gateway.calls[0]
    assert got["intent_id"] == intent["id"]
    parts = got["text"].split(" / ")
    assert parts[0] == "drinking"  # category.primary
    assert _TIME_PART_RE.match(parts[1]), parts[1]  # 時間帯の形式
    assert parts[2] == "天文館"  # location.name
    assert re.fullmatch(r"\d+-\d+人|\d+人", parts[3])  # 人数の表現
    # raw_text(原文)を含まない — 確定値#10
    assert "明日の夜" not in got["text"]
    assert "軽く飲みたい" not in got["text"]


async def test_2_update_clears_and_reembeds(
    api_client, db_engine, worker_env, user_env
):
    """内容更新: PATCH→embedding NULL化→debounce 10秒→再エンベディング→v2完了。"""
    intent = await _create_active(api_client, user_env["headers"])
    await _wait_status(db_engine, "created", intent["id"], intent["version"])
    await _wait_status(
        db_engine, "embedding_completed", intent["id"], intent["version"]
    )
    headers = user_env["headers"]
    v2 = dict(_active_payload())
    v2["raw_text"] = "明日の夜 天文館でしっかり飲みたい"
    r2 = await api_client.patch(f"/v1/intents/{intent['id']}", headers=headers, json=v2)
    assert r2.status_code == 200, r2.text
    assert r2.json()["intent"]["version"] == 2
    cleared = await _fetch_embedding(db_engine, intent["id"])
    assert cleared[0] is False  # §2.3: 内容更新でNULLクリア
    # Worker受信(submit)完了後にClockを進める(ws-1 test_2と同じsettle)
    await asyncio.sleep(1.0)
    worker_env.clock.advance(timedelta(seconds=10))  # debounce窓解放
    await _wait_status(db_engine, "updated", intent["id"], 2)
    await _wait_status(db_engine, "embedding_completed", intent["id"], 2)
    reembedded = await _fetch_embedding(db_engine, intent["id"])
    assert reembedded[0] is True
    assert len(worker_env.gateway.calls) == 2  # 作成+更新(T1 v0.2 §4)


async def test_3_resume_skips_reembed(api_client, db_engine, worker_env, user_env):
    """resume: version+1のupdated→再実行なしでembedding_completed(新version)。"""
    intent = await _create_active(api_client, user_env["headers"])
    await _wait_status(db_engine, "created", intent["id"], intent["version"])
    await _wait_status(
        db_engine, "embedding_completed", intent["id"], intent["version"]
    )
    headers = user_env["headers"]
    resp = await api_client.post(f"/v1/intents/{intent['id']}/pause", headers=headers)
    assert resp.status_code == 200, resp.text
    resp = await api_client.post(f"/v1/intents/{intent['id']}/resume", headers=headers)
    assert resp.status_code == 200, resp.text
    new_version = resp.json()["intent"]["version"]
    assert new_version == intent["version"] + 1  # 05 §6・06 §9
    await asyncio.sleep(1.0)  # Worker受信settle(ws-1 test_8と同じ)
    worker_env.clock.advance(timedelta(seconds=10))
    await _wait_status(db_engine, "updated", intent["id"], new_version)
    await _wait_status(db_engine, "embedding_completed", intent["id"], new_version)
    assert len(worker_env.gateway.calls) == 1  # resumeではGateway不呼(確定値#6)
    row = await _fetch_embedding(db_engine, intent["id"])
    assert row[0] is True  # 埋め込みは保持(クリアされない)


async def test_4_failure_then_backfill(api_client, db_engine, worker_env, user_env):
    """失敗→バックフィル: fail注入→NULLのまま→backfill.run_once→埋め込み+復帰。"""
    worker_env.gateway.fail = True  # 失敗スタブへ(design §4.2)
    intent = await _create_active(api_client, user_env["headers"])
    await _wait_status(db_engine, "created", intent["id"], intent["version"])
    await asyncio.sleep(1.0)  # handle(失敗)の完了を待つ
    row = await _fetch_embedding(db_engine, intent["id"])
    assert row[0] is False  # 06 D-15: NULLのまま保存
    async with db_engine.connect() as conn:
        res = await conn.execute(
            text(
                "SELECT count(*) FROM match_events"
                " WHERE event_type = 'embedding_completed'"
                " AND source_intent_id = :i"
            ),
            {"i": intent["id"]},
        )
        assert res.scalar() == 0  # embedding_completed行なし
    worker_env.gateway.fail = False  # 成功スタブへ差し替え
    backfill = BackfillRunner(
        engine=db_engine,
        embedding=worker_env.embedding,
        interval_sec=1.0,
        batch_limit=50,
    )
    await backfill.run_once()  # 周期を待たず直接1周期実行
    row = await _fetch_embedding(db_engine, intent["id"])
    assert row[0] is True
    assert row[1] == "gemini-embedding-001"
    await _wait_status(db_engine, "embedding_completed", intent["id"], 1)


async def test_5_draft_not_embedded_until_active(
    api_client, db_engine, worker_env, user_env
):
    """draft: 埋め込みなし・Eventなし→active化→created経路で埋め込み+完了。"""
    headers = user_env["headers"]
    draft = await api_client.post(
        "/v1/intents",
        headers=headers,
        json={"raw_text": "下書き", "status": "draft", "structured_intent": None},
    )
    assert draft.status_code == 201, draft.text
    await asyncio.sleep(1.0)
    assert worker_env.gateway.calls == []  # draftはEmbeddingしない(06 §3)
    act = await api_client.patch(
        f"/v1/intents/{draft.json()['intent']['id']}",
        headers=headers,
        json=dict(_active_payload()),
    )
    assert act.status_code == 200, act.text
    intent_id = draft.json()["intent"]["id"]
    version = act.json()["intent"]["version"]
    await _wait_status(db_engine, "created", intent_id, version)  # 作成種と同一経路
    await _wait_status(db_engine, "embedding_completed", intent_id, version)
    assert len(worker_env.gateway.calls) == 1


async def test_6_delete_no_embedding_kick(api_client, db_engine, worker_env, user_env):
    """削除: DELETE→deleted処理はembeddingキックしない(06 §1)。"""
    intent = await _create_active(api_client, user_env["headers"])
    await _wait_status(db_engine, "created", intent["id"], intent["version"])
    await _wait_status(
        db_engine, "embedding_completed", intent["id"], intent["version"]
    )
    resp = await api_client.delete(
        f"/v1/intents/{intent['id']}", headers=user_env["headers"]
    )
    assert resp.status_code == 204, resp.text
    await _wait_status(db_engine, "deleted", intent["id"], intent["version"])
    await asyncio.sleep(1.0)
    assert len(worker_env.gateway.calls) == 1  # deletedでは増えない


async def test_7_duplicate_delivery_embeds_once(
    api_client, db_engine, worker_env, user_env
):
    """重複投入: 同一created Event 2回publish→埋め込み1回(duplicate→handle冪等)。"""
    intent = await _create_active(api_client, user_env["headers"])
    await _wait_status(db_engine, "created", intent["id"], intent["version"])
    await _wait_status(
        db_engine, "embedding_completed", intent["id"], intent["version"]
    )
    # 同一3点組を手動再publish(テストパブリッシャー — 10 §1)
    await worker_env.bus.publish_match_event(
        event_type="created", intent_id=intent["id"], version=intent["version"]
    )
    await asyncio.sleep(2.0)  # 2回目の受領(duplicate)を待つ
    assert len(worker_env.gateway.calls) == 1  # API不呼び出し(has_embedding→直接投入)
    row = await _fetch_embedding(db_engine, intent["id"])
    assert row[0] is True
