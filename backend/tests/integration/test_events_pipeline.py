"""イベント駆動パイプラインのci環境実証(M2 ws-1 design §4.2)。

実HTTP(compose api)・実DB・実Pub/Subエミュレータ(127.0.0.1:8085)。
Worker処理はテストプロセス内Worker(bus=実エミュレータ・clock=FakeClock・
engine=実DB)が担う(design §4.2 — 常設workerはMakefileのtest-ciが停止済み)。
**実行はapi/workerイメージ再ビルド後の make test-ci のみ**(スーパーバイザー
検証時)。試験専用subscriptionを作成/削除し試験間の残余メッセージ干渉を
構造的に排除する(design §5-3: 新規subscriptionは作成以降の配信のみ受ける)。
"""

import asyncio
import os
import sys
import uuid as uuid_mod
from datetime import timedelta

import pytest
from sqlalchemy import text

from latch.core.clock import FakeClock, SystemClock

pytestmark = pytest.mark.integration

# テストプロセスからはport映射経由(コンテナ内はpubsub:8085 — compose.yaml)
os.environ.setdefault("LATCH_PUBSUB_EMULATOR_HOST", "127.0.0.1:8085")

# env設定後にimport(PubsubEventBus構築がPUBSUB_EMULATOR_HOSTを読むため)
from latch.events import PubsubEventBus  # noqa: E402
from latch.settings import Settings  # noqa: E402
from latch.worker.main import Worker  # noqa: E402


async def _instant(_seconds: float) -> None:
    """バックオフ待機の即時化(再試行回数はログで担保 — §2グローバル制約)。"""
    return None


# -- 共通ヘルパ(test_intents_crud_api.py と同じ流儀)--


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
        json={"display_name": "m2ws1", "birth_date": birth_date, "profile": {}},
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


def _invalid_event_log_count(caplog) -> int:
    return sum(
        1
        for r in caplog.records
        if "stage1 invalid event" in r.getMessage() and "attempt=" in r.getMessage()
    )


async def _wait_invalid_event_logs(caplog, minimum: int, timeout: float = 10.0) -> None:
    """stage1のinvalid event警告がcaplogへ出そろうまで待つ。

    DBのquarantined行よりWARNログのcaplog到達が遅れ得る(スーパーバイザー検証で
    assert 0 >= 6 を検出)ため、カウントが揃うまでポーリングする。
    """
    deadline = asyncio.get_event_loop().time() + timeout
    while asyncio.get_event_loop().time() < deadline:
        if _invalid_event_log_count(caplog) >= minimum:
            return
        await asyncio.sleep(0.2)
    pytest.fail(
        f"timeout: stage1 invalid event logs not reached"
        f" ({_invalid_event_log_count(caplog)}/{minimum})"
    )


# -- fixture: 試験専用subscription + テストプロセス内Worker --


class _WorkerEnv:
    def __init__(self, bus, clock, worker, task):
        self.bus = bus
        self.clock = clock
        self.worker = worker
        self.task = task


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
    worker = Worker(
        clock=clock, settings=settings, bus=bus, engine=db_engine, sleep=_instant
    )
    task = asyncio.create_task(worker.run())
    await asyncio.sleep(0.1)  # subscribe開始を待つ
    try:
        yield _WorkerEnv(bus=bus, clock=clock, worker=worker, task=task)
    finally:
        worker.request_shutdown()
        try:
            await asyncio.wait_for(task, timeout=5.0)
        except TimeoutError:
            task.cancel()
        # 残余メッセージを次試験へ残さない。**closeの前に**削除する
        # (close後のgRPCチャネルはクローズ済みで "Cannot invoke RPC on
        # closed channel" になる — スーパーバイザー検証で検出)
        bus.delete_subscription()
        await bus.close()


@pytest.fixture
async def user_env(api_client, db_engine):
    """subject実行ごと一意のuser。後始末でmatch_events・intent行を消す。"""
    subject = f"m2ws1-{uuid_mod.uuid4().hex[:12]}"
    headers, user_id = await _register(api_client, subject)
    st = {"headers": headers, "user_id": user_id}
    try:
        yield st
    finally:
        async with db_engine.begin() as conn:
            # test_4がfixtureで入れるmatch_candidatesを先に消す(FK:
            # fk_match_candidates_intent_a。supervisor検証4巡目のteardown修正)
            await conn.execute(
                text(
                    "DELETE FROM match_candidates WHERE intent_a_id IN"
                    " (SELECT id FROM intents WHERE user_id = :uid)"
                    " OR intent_b_id IN (SELECT id FROM intents WHERE user_id = :uid)"
                ),
                {"uid": user_id},
            )
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
    # POST /v1/intents応答は {"intent": {...}} ラップ(IntentEnvelope。
    # M1 test_intents_crud_api.py:141 と同一流儀)
    return resp.json()["intent"]


# -- §4.2の8試験 --


async def test_1_e2e_create_event_processed(
    api_client, db_engine, worker_env, user_env
):
    """E2E(作成): POST→API publish→Worker受信→processed(3点組も検証)。#2・#13"""
    intent = await _create_active(api_client, user_env["headers"])
    row = await _wait_status(db_engine, "created", intent["id"], intent["version"])
    assert row[0] == "processed"
    assert row[1]["version"] == intent["version"]  # メッセージ内容(3点組)


async def test_2_debounce_merges_updates(api_client, db_engine, worker_env, user_env):
    """PATCH 2回→窓統合→最新versionのみ処理・全行processed。#6-1"""
    intent = await _create_active(api_client, user_env["headers"])
    await _wait_status(db_engine, "created", intent["id"], intent["version"])
    headers = user_env["headers"]
    v2 = dict(_active_payload())
    v2["raw_text"] = "明日の夜 天文館でしっかり飲みたい"
    r2 = await api_client.patch(f"/v1/intents/{intent['id']}", headers=headers, json=v2)
    assert r2.status_code == 200, r2.text
    # Worker受信(submit)完了後にClockを進める(実時間settle — advanceがsubmit前に
    # 走るとrelease_atがClock進行後の時刻+10秒となり到達不能・窓が解放されない)
    await asyncio.sleep(1.0)
    worker_env.clock.advance(timedelta(seconds=3))
    v3 = dict(_active_payload())
    v3["raw_text"] = "明後日の夜 天文館で軽く飲みたい"
    r3 = await api_client.patch(f"/v1/intents/{intent['id']}", headers=headers, json=v3)
    assert r3.status_code == 200, r3.text
    await asyncio.sleep(1.0)
    worker_env.clock.advance(timedelta(seconds=10))  # 窓解放
    row3 = await _wait_status(db_engine, "updated", intent["id"], 3)
    assert row3[0] == "processed" and "discard_reason" not in row3[1]
    row2 = await _wait_status(db_engine, "updated", intent["id"], 2)
    assert row2[0] == "processed"  # 窓吸収行のprocessed閉包
    assert row2[1].get("discard_reason") == "debounced_superceded"


async def test_3_draft_to_active_emits_created(
    api_client, db_engine, worker_env, user_env
):
    """draft作成(発行なし)→active化→作成種Eventが即時処理。#6-0"""
    headers = user_env["headers"]
    draft = await api_client.post(
        "/v1/intents",
        headers=headers,
        json={"raw_text": "下書き", "status": "draft", "structured_intent": None},
    )
    assert draft.status_code == 201, draft.text
    intent_id = draft.json()["intent"]["id"]  # IntentEnvelopeラップ
    async with db_engine.connect() as conn:
        res = await conn.execute(
            text("SELECT count(*) FROM match_events WHERE source_intent_id = :i"),
            {"i": intent_id},
        )
        assert res.scalar() == 0  # draftは発行しない
    act = await api_client.patch(
        f"/v1/intents/{intent_id}",
        headers=headers,
        json=dict(_active_payload()),
    )
    assert act.status_code == 200, act.text
    row = await _wait_status(
        db_engine, "created", intent_id, act.json()["intent"]["version"]
    )
    assert row[0] == "processed"


async def test_4_delete_event_closes_candidates(
    api_client, db_engine, worker_env, user_env
):
    """fixtureでmatch_candidates行を直接INSERT→DELETE→該当行がclosed。#7"""
    i1 = await _create_active(api_client, user_env["headers"])
    await _wait_status(db_engine, "created", i1["id"], i1["version"])
    # 同一userの2つ目(pending回避のためactive上限内)
    i2 = await _create_active(api_client, user_env["headers"])
    await _wait_status(db_engine, "created", i2["id"], i2["version"])
    async with db_engine.begin() as conn:
        await conn.execute(
            text(
                "INSERT INTO match_candidates"
                " (intent_a_id, intent_b_id, intent_a_version, intent_b_version,"
                "  status, created_at, updated_at)"
                " VALUES (:a, :b, :av, :bv, 'candidate', :now, :now)"
            ),
            {
                "a": i1["id"],
                "b": i2["id"],
                "av": i1["version"],
                "bv": i2["version"],
                "now": SystemClock().now(),
            },
        )
    resp = await api_client.delete(
        f"/v1/intents/{i1['id']}", headers=user_env["headers"]
    )
    assert resp.status_code == 204, resp.text
    row = await _wait_status(db_engine, "deleted", i1["id"], i1["version"])
    assert row[0] == "processed"
    async with db_engine.connect() as conn:
        res = await conn.execute(
            text(
                "SELECT count(*) FROM match_candidates"
                " WHERE (intent_a_id = :i OR intent_b_id = :i) AND status <> 'closed'"
            ),
            {"i": i1["id"]},
        )
        assert res.scalar() == 0  # 候補はclosed(06 §1)


async def test_5_missing_intent_processed_discard(db_engine, worker_env):
    """存在しないintent_idのEvent→processed+discard_reason(隔離しない)。#8・#14"""
    ghost = uuid_mod.uuid4()
    await worker_env.bus.publish_match_event(
        event_type="created", intent_id=ghost, version=1
    )
    row = await _wait_status(db_engine, "created", ghost, 1)
    assert row[0] == "processed"
    assert row[1].get("discard_reason") == "intent_not_found"
    async with db_engine.begin() as conn:  # 後始末(直指定削除)
        await conn.execute(
            text("DELETE FROM match_events WHERE source_intent_id = :i"), {"i": ghost}
        )


async def test_6_poison_payload_quarantined(db_engine, worker_env, caplog):
    """毒ペイロード(version欠落)→再試行5回→quarantined+理由保持。後続滞らず。#8・#14"""
    await worker_env.bus.publish_raw(b'{"event_type": "updated"}')  # version欠落
    async with db_engine.connect() as conn:
        deadline = asyncio.get_event_loop().time() + 20.0
        while asyncio.get_event_loop().time() < deadline:
            res = await conn.execute(
                text(
                    "SELECT count(*) FROM match_events"
                    " WHERE status = 'quarantined' AND payload ? 'failure_reason'"
                )
            )
            if res.scalar() >= 1:
                break
            await asyncio.sleep(0.2)
        else:
            pytest.fail("poison payload not quarantined")
    # 再試行5回の確認(実ログ — 10 §4.7)。WARNのcaplog到達はDB行コミットより
    # 遅れ得るため、揃うまで待ってから数える(assert 0>=6 を検出した対策)
    await _wait_invalid_event_logs(caplog, 6)
    assert _invalid_event_log_count(caplog) >= 6  # 初回+再試行5回の警告ログ
    # 後続が滞らない: 正常Eventを続けて投入しprocessedになる
    normal = uuid_mod.uuid4()
    await worker_env.bus.publish_match_event(
        event_type="created", intent_id=normal, version=1
    )
    row = await _wait_status(db_engine, "created", normal, 1)
    assert row[0] == "processed"
    async with db_engine.begin() as conn:  # 後始末
        await conn.execute(
            text(
                "DELETE FROM match_events"
                " WHERE source_intent_id = :normal OR payload ? 'failure_reason'"
            ),
            {"normal": normal},
        )


async def test_7_duplicate_delivery_processed_once(db_engine, worker_env):
    """同一3点組2回publish→行1行・処理1回(2回目はackのみ)。#5・#14"""
    iid = uuid_mod.uuid4()
    await worker_env.bus.publish_match_event(
        event_type="created", intent_id=iid, version=1
    )
    await _wait_status(db_engine, "created", iid, 1)
    await worker_env.bus.publish_match_event(
        event_type="created", intent_id=iid, version=1
    )
    await asyncio.sleep(2.0)  # 2回目の受領を待つ
    async with db_engine.connect() as conn:
        res = await conn.execute(
            text(
                "SELECT count(*) FROM match_events"
                " WHERE event_type='created' AND source_intent_id = :i"
                " AND payload->>'version' = '1'"
            ),
            {"i": iid},
        )
        assert res.scalar() == 1  # 二重生成なし(UNIQUE+行確保)
    async with db_engine.begin() as conn:
        await conn.execute(
            text("DELETE FROM match_events WHERE source_intent_id = :i"), {"i": iid}
        )


async def test_8_resume_update_event(api_client, db_engine, worker_env, user_env):
    """pause→resume→version+1のupdate種→debounce経由で処理。#9"""
    intent = await _create_active(api_client, user_env["headers"])
    await _wait_status(db_engine, "created", intent["id"], intent["version"])
    headers = user_env["headers"]
    resp = await api_client.post(f"/v1/intents/{intent['id']}/pause", headers=headers)
    assert resp.status_code == 200, resp.text
    resp = await api_client.post(f"/v1/intents/{intent['id']}/resume", headers=headers)
    assert resp.status_code == 200, resp.text
    new_version = resp.json()["intent"]["version"]  # IntentEnvelopeラップ
    assert new_version == intent["version"] + 1  # 05 §6・06 §9
    # Worker受信(submit)完了後にClockを進ける(test_2と同じsettle — 時計進行レース回避)
    await asyncio.sleep(1.0)
    worker_env.clock.advance(timedelta(seconds=10))  # debounce窓解放
    row = await _wait_status(db_engine, "updated", intent["id"], new_version)
    assert row[0] == "processed"
