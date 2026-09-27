"""intents CRUD APIのci環境実証(M1 ws-3 design §4.2)。

実HTTP(compose api=127.0.0.1:8000)・実DB・実ジオコーディング。
**実行はapiイメージ再ビルド後の make test-ci のみ**(compose upはapiイメージ
を再ビルドしない — STATUS運用ルール4。スーパーバイザー検証時に実行)。
正転地名は「天文館」に固定(fixture osm_sample.xml と実取り込みデータの双方
に存在 — design §4.2前提)。subjectは実行ごとにユニーク、行は試験内で後始末
(共有ci-db汚染回避 — M0 ws-3と同じ規約)。Intent行は試験データの後始末として
物理DELETEする(仕様の削除経路ではない — design §4.2-10)。
"""

import asyncio
import json
import sys
import uuid as uuid_mod
from datetime import date, timedelta

import pytest
from sqlalchemy import text

from latch.core.clock import SystemClock

pytestmark = pytest.mark.integration


def _unique_subject(prefix: str) -> str:
    return f"m1ws3-{prefix}-{uuid_mod.uuid4().hex[:12]}"


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
    """token発行→User登録→(headers, user_id)を返す(test_users_api流儀)。"""
    idp_token = await _cli_idp_token("google", subject)
    tok = await api_client.post(
        "/v1/auth/token", json={"provider": "google", "idp_token": idp_token}
    )
    assert tok.status_code == 200, tok.text
    headers = {"Authorization": f"Bearer {tok.json()['access_token']}"}
    created = await api_client.post(
        "/v1/users",
        headers=headers,
        json={"display_name": "ws3", "birth_date": birth_date, "profile": {}},
    )
    assert created.status_code == 201, created.text
    return headers, created.json()["user"]["id"]


def _birth_19() -> str:
    """実行日(jst_date)時点で常に19歳(design §4.2-4。usersの_birth_17と同型)。"""
    today = SystemClock().jst_date()
    return (
        date(today.year - 20, today.month, today.day) + timedelta(days=1)
    ).isoformat()


def _future(hours: float) -> str:
    """api実Clock(SystemClock)基準の未来/過去ISO時刻。境界から離れた値だけ
    使う(apiとテストプロセスの時計は数秒ずれ得る)。"""
    now = SystemClock().now()
    return (now + timedelta(hours=hours)).isoformat()


def _structured() -> dict:
    return {
        "category": {"primary": "drinking", "secondary": "焼肉"},
        "alcohol_involved": True,
        "time": {"start": _future(3), "end": _future(6)},
        "location": {"name": "天文館", "radius_m": 2000},
        "budget": {"max": 5000, "currency": "JPY"},
        "participants": {"min": 2, "max": 4},
        "visibility": "hidden_until_match",
        "notification_level": "proposals_only",
        "expires_at": _future(6),
        "soft_constraints": ["軽く飲みたい"],
        "ng_unverifiable": ["会社関係の人は避けたい"],
        "negative_constraints": [],
    }


def _active_payload(
    structured: dict | None = None, raw: str = "今夜20時から天文館で軽く飲みたい"
) -> dict:
    return {
        "raw_text": raw,
        "status": "active",
        "structured_intent": structured if structured is not None else _structured(),
    }


async def _cleanup(db_engine, user_id, subject: str) -> None:
    async with db_engine.begin() as conn:
        if user_id:
            await conn.execute(
                text(
                    "DELETE FROM match_events WHERE source_intent_id IN "
                    "(SELECT id FROM intents WHERE user_id = :uid)"
                ),
                {"uid": user_id},
            )
            await conn.execute(
                text("DELETE FROM intents WHERE user_id = :uid"), {"uid": user_id}
            )
        await conn.execute(
            text("DELETE FROM users WHERE auth_subject = :s"), {"s": subject}
        )


def _load_jsonb(value):
    return json.loads(value) if isinstance(value, str) else value


# --- §4.2-1 active作成フルフロー ---


async def test_1_active_create_full_flow(api_client, db_engine):
    subject = _unique_subject("flow")
    user_id = None
    try:
        headers, user_id = await _register(api_client, subject)
        resp = await api_client.post(
            "/v1/intents", headers=headers, json=_active_payload()
        )
        assert resp.status_code == 201, resp.text
        intent = resp.json()["intent"]
        assert intent["status"] == "active"
        assert intent["version"] == 1
        assert intent["expires_at"]
        intent_id = intent["id"]

        got = await api_client.get(f"/v1/intents/{intent_id}", headers=headers)
        assert got.status_code == 200, got.text
        body = got.json()["intent"]
        assert body["raw_text"] == "今夜20時から天文館で軽く飲みたい"
        assert body["structured_intent"]["location"]["name"] == "天文館"
        assert body["structured_intent"]["time"]["end"]  # 補完後(指定値)
        assert body["structured_data"]["location_name"] == "天文館"

        async with db_engine.connect() as conn:
            row = (
                (
                    await conn.execute(
                        text(
                            "SELECT geo_center IS NOT NULL AS has_geo, "
                            "structured_data FROM intents WHERE id = :i"
                        ),
                        {"i": intent_id},
                    )
                )
                .mappings()
                .first()
            )
            assert row["has_geo"] is True
            assert _load_jsonb(row["structured_data"])["location_name"] == "天文館"
            evs = (
                (
                    await conn.execute(
                        text(
                            "SELECT event_type, payload, status FROM match_events "
                            "WHERE source_intent_id = :i"
                        ),
                        {"i": intent_id},
                    )
                )
                .mappings()
                .all()
            )
        assert len(evs) == 1
        assert evs[0]["event_type"] == "created"
        assert evs[0]["status"] == "pending"
        assert _load_jsonb(evs[0]["payload"]) == {"version": 1}
    finally:
        await _cleanup(db_engine, user_id, subject)


# --- §4.2-2 draft作成 ---


async def test_2_draft_create(api_client, db_engine):
    subject = _unique_subject("draft")
    user_id = None
    try:
        headers, user_id = await _register(api_client, subject)
        resp = await api_client.post(
            "/v1/intents",
            headers=headers,
            json={"raw_text": "後で書く", "status": "draft"},
        )
        assert resp.status_code == 201, resp.text
        intent_id = resp.json()["intent"]["id"]
        async with db_engine.connect() as conn:
            row = (
                (
                    await conn.execute(
                        text(
                            "SELECT geo_center IS NULL AS no_geo, expires_at, "
                            "visibility FROM intents WHERE id = :i"
                        ),
                        {"i": intent_id},
                    )
                )
                .mappings()
                .first()
            )
            assert row["no_geo"] is True
            assert row["expires_at"] is None
            assert row["visibility"] == "hidden_until_match"
            count = await conn.scalar(
                text("SELECT count(*) FROM match_events WHERE source_intent_id = :i"),
                {"i": intent_id},
            )
        assert count == 0
        listed = await api_client.get(
            "/v1/intents", headers=headers, params={"status": "draft"}
        )
        assert listed.status_code == 200
        assert [i["id"] for i in listed.json()["items"]] == [intent_id]
    finally:
        await _cleanup(db_engine, user_id, subject)


# --- §4.2-3 draft→active ---


async def test_3_draft_to_active(api_client, db_engine):
    subject = _unique_subject("d2a")
    user_id = None
    try:
        headers, user_id = await _register(api_client, subject)
        structured = _structured()

        # (a) 同一内容のactive化: version=1据え置き・created Event version=1
        payload = _active_payload(structured=structured)
        created = await api_client.post(
            "/v1/intents",
            headers=headers,
            json={
                "raw_text": payload["raw_text"],
                "status": "draft",
                "structured_intent": structured,
            },
        )
        assert created.status_code == 201, created.text
        same_id = created.json()["intent"]["id"]
        activated = await api_client.patch(
            f"/v1/intents/{same_id}",
            headers=headers,
            json=payload,
        )
        assert activated.status_code == 200, activated.text
        assert activated.json()["intent"]["status"] == "active"
        assert activated.json()["intent"]["version"] == 1
        async with db_engine.connect() as conn:
            row = (
                (
                    await conn.execute(
                        text(
                            "SELECT geo_center IS NOT NULL AS has_geo, expires_at "
                            "FROM intents WHERE id = :i"
                        ),
                        {"i": same_id},
                    )
                )
                .mappings()
                .first()
            )
            assert row["has_geo"] is True
            assert row["expires_at"] is not None  # 補完
            ev = (
                (
                    await conn.execute(
                        text(
                            "SELECT event_type, payload FROM match_events "
                            "WHERE source_intent_id = :i"
                        ),
                        {"i": same_id},
                    )
                )
                .mappings()
                .one()
            )
        assert ev["event_type"] == "created"
        assert _load_jsonb(ev["payload"]) == {"version": 1}

        # (b) 内容変更を伴うactive化: version=2+created Event version=2
        changed = dict(structured)
        changed["participants"] = {"min": 3, "max": 4}
        created2 = await api_client.post(
            "/v1/intents",
            headers=headers,
            json={
                "raw_text": payload["raw_text"],
                "status": "draft",
                "structured_intent": structured,
            },
        )
        assert created2.status_code == 201
        changed_id = created2.json()["intent"]["id"]
        activated2 = await api_client.patch(
            f"/v1/intents/{changed_id}",
            headers=headers,
            json=_active_payload(structured=changed, raw=payload["raw_text"]),
        )
        assert activated2.status_code == 200, activated2.text
        assert activated2.json()["intent"]["version"] == 2
        async with db_engine.connect() as conn:
            ev2 = (
                (
                    await conn.execute(
                        text(
                            "SELECT event_type, payload FROM match_events "
                            "WHERE source_intent_id = :i"
                        ),
                        {"i": changed_id},
                    )
                )
                .mappings()
                .one()
            )
        assert ev2["event_type"] == "created"
        assert _load_jsonb(ev2["payload"]) == {"version": 2}
    finally:
        await _cleanup(db_engine, user_id, subject)


# --- §4.2-4 検証422一式(active/draft対照) ---


async def test_4_validation_errors_active_vs_draft(api_client, db_engine):
    subject = _unique_subject("val")
    user_id = None
    try:
        headers, user_id = await _register(api_client, subject)

        async def _code(payload):
            resp = await api_client.post("/v1/intents", headers=headers, json=payload)
            return resp.status_code, (resp.json().get("error", {}).get("code"))

        base = _structured()
        no_category = dict(base, category=None)
        assert await _code(_active_payload(structured=no_category)) == (
            422,
            "VALIDATION_ERROR",
        )
        past = dict(base, time={"start": _future(-1)})
        assert await _code(_active_payload(structured=past)) == (
            422,
            "VALIDATION_ERROR",
        )
        far = dict(base, time={"start": _future(8 * 24)})
        assert await _code(_active_payload(structured=far)) == (
            422,
            "VALIDATION_ERROR",
        )
        past_exp = dict(base, expires_at=_future(-1))
        assert await _code(_active_payload(structured=past_exp)) == (
            422,
            "VALIDATION_ERROR",
        )
        unknown_place = dict(base, location={"name": "存在しない地名テスト2026"})
        assert await _code(_active_payload(structured=unknown_place)) == (
            422,
            "GEOCODING_FAILED",
        )
        # draftは同内容(過去時刻・必須3欠落)が受理される対照
        ok_past = await _code(
            {
                "raw_text": "下書きなら過去でも",
                "status": "draft",
                "structured_intent": past,
            }
        )
        assert ok_past[0] == 201
        ok_missing = await _code(
            {
                "raw_text": "下書きなら欠けてても",
                "status": "draft",
                "structured_intent": no_category,
            }
        )
        assert ok_missing[0] == 201
    finally:
        await _cleanup(db_engine, user_id, subject)


async def test_4b_under_age_drinking_rejected(api_client, db_engine):
    subject = _unique_subject("u19")
    user_id = None
    try:
        headers, user_id = await _register(api_client, subject, birth_date=_birth_19())
        resp = await api_client.post(
            "/v1/intents", headers=headers, json=_active_payload()
        )
        assert resp.status_code == 422, resp.text
        assert resp.json()["error"]["code"] == "UNDER_AGE"
    finally:
        await _cleanup(db_engine, user_id, subject)


# --- §4.2-5 PATCH active更新 ---


async def test_5_patch_active_update(api_client, db_engine):
    subject = _unique_subject("upd")
    user_id = None
    try:
        headers, user_id = await _register(api_client, subject)
        created = await api_client.post(
            "/v1/intents", headers=headers, json=_active_payload()
        )
        intent_id = created.json()["intent"]["id"]

        async def _geo_lon():
            async with db_engine.connect() as conn:
                return await conn.scalar(
                    text(
                        "SELECT ST_X(geo_center::geometry) FROM intents WHERE id = :i"
                    ),
                    {"i": intent_id},
                )

        before_lon = await _geo_lon()
        moved = dict(_structured())
        moved["location"] = {"name": "鹿児島市天文館一丁目", "radius_m": 1000}
        resp = await api_client.patch(
            f"/v1/intents/{intent_id}",
            headers=headers,
            json=_active_payload(structured=moved, raw="明日も天文館で"),
        )
        assert resp.status_code == 200, resp.text
        body = resp.json()["intent"]
        assert body["version"] == 2
        assert body["structured_intent"]["location"]["name"] == "鹿児島市天文館一丁目"
        after_lon = await _geo_lon()
        assert after_lon is not None and after_lon != before_lon  # 再ジオコーディング
        async with db_engine.connect() as conn:
            ev = (
                (
                    await conn.execute(
                        text(
                            "SELECT event_type, payload FROM match_events "
                            "WHERE source_intent_id = :i ORDER BY created_at"
                        ),
                        {"i": intent_id},
                    )
                )
                .mappings()
                .all()
            )
        assert len(ev) == 2
        assert ev[1]["event_type"] == "updated"
        assert _load_jsonb(ev[1]["payload"]) == {"version": 2}
    finally:
        await _cleanup(db_engine, user_id, subject)


# --- §4.2-6 pause/resume ---


async def test_6_pause_resume(api_client, db_engine):
    subject = _unique_subject("pr")
    user_id = None
    try:
        headers, user_id = await _register(api_client, subject)
        created = await api_client.post(
            "/v1/intents", headers=headers, json=_active_payload()
        )
        intent_id = created.json()["intent"]["id"]

        paused = await api_client.post(
            f"/v1/intents/{intent_id}/pause", headers=headers
        )
        assert paused.status_code == 200, paused.text
        assert paused.json()["intent"]["status"] == "paused"
        assert paused.json()["intent"]["version"] == 1  # 不変
        async with db_engine.connect() as conn:
            count1 = await conn.scalar(
                text("SELECT count(*) FROM match_events WHERE source_intent_id = :i"),
                {"i": intent_id},
            )
        assert count1 == 1  # pauseでは増えない

        resumed = await api_client.post(
            f"/v1/intents/{intent_id}/resume", headers=headers
        )
        assert resumed.status_code == 200, resumed.text
        assert resumed.json()["intent"]["status"] == "active"
        assert resumed.json()["intent"]["version"] == 2
        async with db_engine.connect() as conn:
            evs = (
                (
                    await conn.execute(
                        text(
                            "SELECT event_type, payload FROM match_events "
                            "WHERE source_intent_id = :i ORDER BY created_at"
                        ),
                        {"i": intent_id},
                    )
                )
                .mappings()
                .all()
            )
        assert len(evs) == 2
        assert evs[1]["event_type"] == "updated"
        assert _load_jsonb(evs[1]["payload"]) == {"version": 2}
    finally:
        await _cleanup(db_engine, user_id, subject)


# --- §4.2-7 DELETE ---


async def test_7_delete(api_client, db_engine):
    subject = _unique_subject("del")
    user_id = None
    try:
        headers, user_id = await _register(api_client, subject)
        active = await api_client.post(
            "/v1/intents", headers=headers, json=_active_payload()
        )
        active_id = active.json()["intent"]["id"]
        draft = await api_client.post(
            "/v1/intents",
            headers=headers,
            json={"raw_text": "消す下書き", "status": "draft"},
        )
        draft_id = draft.json()["intent"]["id"]

        for target in (active_id, draft_id):
            resp = await api_client.delete(f"/v1/intents/{target}", headers=headers)
            assert resp.status_code == 204
            async with db_engine.connect() as conn:
                status = await conn.scalar(
                    text("SELECT status FROM intents WHERE id = :i"), {"i": target}
                )
                ev = (
                    (
                        await conn.execute(
                            text(
                                "SELECT event_type, payload FROM match_events "
                                "WHERE source_intent_id = :i"
                            ),
                            {"i": target},
                        )
                    )
                    .mappings()
                    .one()
                )
            assert status == "cancelled"
            assert ev["event_type"] == "deleted"
    finally:
        await _cleanup(db_engine, user_id, subject)


# --- §4.2-8 認可 ---


async def test_8_authorization(api_client, db_engine):
    owner_subject = _unique_subject("own")
    other_subject = _unique_subject("oth")
    owner_id = None
    other_id = None
    try:
        owner_headers, owner_id = await _register(api_client, owner_subject)
        other_headers, other_id = await _register(api_client, other_subject)
        created = await api_client.post(
            "/v1/intents", headers=owner_headers, json=_active_payload()
        )
        intent_id = created.json()["intent"]["id"]
        for method, path, kwargs in [
            ("get", f"/v1/intents/{intent_id}", {}),
            ("patch", f"/v1/intents/{intent_id}", {"json": _active_payload()}),
            ("delete", f"/v1/intents/{intent_id}", {}),
            ("post", f"/v1/intents/{intent_id}/pause", {}),
        ]:
            resp = await getattr(api_client, method)(
                path, headers=other_headers, **kwargs
            )
            assert resp.status_code == 403, (method, resp.text)
            assert resp.json()["error"]["code"] == "FORBIDDEN"
        missing = await api_client.get(
            f"/v1/intents/{uuid_mod.uuid4()}", headers=owner_headers
        )
        assert missing.status_code == 404
        assert missing.json()["error"]["code"] == "NOT_FOUND"
        no_auth = await api_client.get("/v1/intents")
        assert no_auth.status_code == 401
        assert no_auth.json()["error"]["code"] == "UNAUTHENTICATED"
    finally:
        await _cleanup(db_engine, other_id, other_subject)
        await _cleanup(db_engine, owner_id, owner_subject)


# --- §4.2-9 cursor ---


async def test_9_cursor_pagination_and_status_filter(api_client, db_engine):
    subject = _unique_subject("cur")
    user_id = None
    try:
        headers, user_id = await _register(api_client, subject)
        made = []
        for i in range(3):
            resp = await api_client.post(
                "/v1/intents",
                headers=headers,
                json=_active_payload(raw=f"ページング試験{i}"),
            )
            assert resp.status_code == 201, resp.text
            made.append(resp.json()["intent"]["id"])
        page1 = await api_client.get(
            "/v1/intents", headers=headers, params={"limit": 2}
        )
        assert page1.status_code == 200
        body1 = page1.json()
        assert len(body1["items"]) == 2
        assert body1["next_cursor"]
        page2 = await api_client.get(
            "/v1/intents",
            headers=headers,
            params={"limit": 2, "cursor": body1["next_cursor"]},
        )
        body2 = page2.json()
        assert len(body2["items"]) == 1
        assert body2["next_cursor"] is None
        collected = {i["id"] for i in body1["items"] + body2["items"]}
        assert collected == set(made)  # 重複・欠落なし
        # statusフィルタの絞り込み
        draft = await api_client.post(
            "/v1/intents",
            headers=headers,
            json={"raw_text": "下書き1件", "status": "draft"},
        )
        draft_id = draft.json()["intent"]["id"]
        filtered = await api_client.get(
            "/v1/intents", headers=headers, params={"status": "draft"}
        )
        assert [i["id"] for i in filtered.json()["items"]] == [draft_id]
    finally:
        await _cleanup(db_engine, user_id, subject)
