"""ws-7ハーネスの共通ヘルパ(べき等シード・ペア作成・収束待ち)。

投入順序はusers→consent→intents(ws-5同意必須)。仮想Clockはシードbaseへset
する(ws-3ハーネスと同一の適応: 実行日がbase以降でも、Redis不在(down -v
直後)からのsetは制御開始であり・以後の判定はbase基準で決定的になる)。
"""

from __future__ import annotations

import asyncio
import time
import uuid
from datetime import datetime, timedelta

import yaml

from conftest import (
    EXPECTED_HEAD,
    KEYS_DIR,
    SEED_PATH,
    auth_header,
    get_clock,
)

JST = timedelta(hours=9)
_PROBE_PLACE = "天文館"  # 対配置Intentの地名(ジオコーディング成立・ws-2語彙)


async def wait_until(
    cond,
    *,
    timeout_s: float,
    interval_s: float = 5.0,
    what: str = "condition",
) -> None:
    """async条件が成立するまでinterval_s間隔でポーリングする(上限で失敗)。"""
    deadline = time.monotonic() + timeout_s
    while True:
        if await cond():
            return
        if time.monotonic() >= deadline:
            raise AssertionError(f"タイムアウト({timeout_s}s): {what}")
        await asyncio.sleep(interval_s)


async def ensure_env_prerequisites(db) -> None:  # noqa: ANN001
    """環境前提の名前つき失敗(原因不明の連鎖失敗を防ぐ)。"""
    ver = await db.fetchval("SELECT version_num FROM alembic_version")
    if ver != EXPECTED_HEAD:
        raise AssertionError(
            f"alembic headが{EXPECTED_HEAD}ではありません(実際={ver})。"
            "make staging-nfr-resetを再実行してください"
        )
    n_geo = await db.fetchval("SELECT count(*) FROM geofeatures")
    if not n_geo:
        raise AssertionError("geofeaturesが空です。staging-geo-importを実行")
    if not (KEYS_DIR / "idp_test_private.pem").exists():
        raise AssertionError(".staging/keysがありません。make staging-keysを実行")


def load_seed() -> dict:
    return yaml.safe_load(SEED_PATH.read_text(encoding="utf-8"))


def _seed_intent_payload(i: dict) -> dict:
    """POST /v1/intentsのbody(ws-3 helpers_seedと同型の組立)。"""
    s = i["structured"]
    time_part: dict = {"start": s["time"]["start"]}
    if s["time"].get("end"):
        time_part["end"] = s["time"]["end"]
    location_part: dict = {"name": s["location"]["name"]}
    if s["location"].get("radius_m"):
        location_part["radius_m"] = s["location"]["radius_m"]
    budget_part = None
    if s.get("budget", {}).get("max") is not None:
        budget_part = {"max": s["budget"]["max"]}
    return {
        "raw_text": i["raw_text"],
        "status": "active",
        "structured_intent": {
            "category": {
                "primary": s["category"]["primary"],
                "secondary": s["category"].get("secondary"),
            },
            "alcohol_involved": s["alcohol_involved"],
            "time": time_part,
            "location": location_part,
            "budget": budget_part,
            "participants": {
                "min": s["participants"]["min"],
                "max": s["participants"]["max"],
            },
            "soft_constraints": s.get("soft_constraints") or [],
            "visibility": s["visibility"],
            "notification_level": s["notification_level"],
        },
    }


async def _pending_events(db) -> int:  # noqa: ANN001
    return await db.fetchval("SELECT count(*) FROM match_events WHERE status='pending'")


async def _evaluated_count(db) -> int:  # noqa: ANN001
    return await db.fetchval(
        "SELECT count(*) FROM match_candidates WHERE jev_result IS NOT NULL"
    )


async def wait_events_done(db, *, timeout_s: float = 240) -> None:  # noqa: ANN001
    await wait_until(
        lambda: _pending_events(db) == 0,
        timeout_s=timeout_s,
        interval_s=5,
        what="Event処理完了(pending=0・debounce 10秒+worker処理込み)",
    )


async def wait_candidates_stable(
    db, *, timeout_s: float = 150, interval_s: float = 10
) -> None:  # noqa: ANN001
    """候補評価数が10秒間隔で2回連続不変になるまで待つ(ws-3と同型)。"""
    prev = await _evaluated_count(db)
    deadline = time.monotonic() + timeout_s
    while True:
        if time.monotonic() > deadline:
            raise AssertionError(f"タイムアウト({timeout_s}s): 候補評価の安定")
        await asyncio.sleep(interval_s)
        cur = await _evaluated_count(db)
        if cur == prev:
            return
        prev = cur


async def wait_pipeline_settled(db, *, timeout_s: float = 240) -> None:  # noqa: ANN001
    await wait_events_done(db, timeout_s=timeout_s)
    await wait_candidates_stable(db)


async def ensure_seed(api, db) -> dict:  # noqa: ANN001
    """シード投入(べき等・design §4.3手順1の器)。

    users行がシード人数以上ならスキップ(部分実行での再投入防止)。無ければ
    ws-3 helpers_seed.seed_allと同一経路(users→consent→intents)で投入し、
    仮想Clockをシードbaseへsetする。
    """
    await ensure_env_prerequisites(db)
    seed = load_seed()
    n_users = await db.fetchval("SELECT count(*) FROM users")
    if n_users >= len(seed["users"]):
        return {"skipped": True, "users": n_users, "intents": None}

    clock = get_clock()
    clock.set(datetime.fromisoformat(seed["meta"]["base_datetime"]))
    # ↑baseが実時間より過去でも、down -v直後のRedis不在からのset=制御開始
    #  (オフセット作成)であり後退ではない。以後の判定はbase基準で決定的

    for u in seed["users"]:
        body = {
            "display_name": u["display_name"],
            "birth_date": u["birth_date"],
            "profile": {"bio": (u.get("profile") or {}).get("bio")},
        }
        r = await api.post("/v1/users", headers=auth_header(u["id"]), json=body)
        assert r.status_code == 201, (
            f"users投入失敗 {u['id']}: {r.status_code} {r.text}"
        )
        rc = await api.post("/v1/users/me/consent", headers=auth_header(u["id"]))
        assert rc.status_code == 200, f"consent失敗 {u['id']}: {rc.status_code}"

    su_to_uuid: dict[str, str] = {}
    for i in seed["intents"]:
        r = await api.post(
            "/v1/intents",
            headers=auth_header(i["user"]),
            json=_seed_intent_payload(i),
        )
        assert r.status_code == 201, (
            f"intents投入失敗 {i['id']}: {r.status_code} {r.text}"
        )
        su_to_uuid[i["id"]] = r.json()["intent"]["id"]

    await wait_pipeline_settled(db)
    return {"skipped": False, "users": len(seed["users"]), "intents": su_to_uuid}


def intent_payload(*, seq: int, now: datetime, start_hours: float = 3.0) -> dict:
    """対配置Intentのペイロード(同地名・同カテゴリ・stub Jevで提案成立)。"""
    start = now + timedelta(hours=start_hours)
    return {
        "raw_text": f"ws7試験{seq}: {_PROBE_PLACE}あたりで飲みながら話したい",
        "status": "active",
        "structured_intent": {
            "category": {"primary": "drinking", "secondary": None},
            "alcohol_involved": True,
            "time": {
                "start": start.isoformat(),
                "end": (start + timedelta(hours=2)).isoformat(),
            },
            "location": {"name": _PROBE_PLACE},
            "budget": {"max": 3000},
            "participants": {"min": 2, "max": 4},
            "soft_constraints": [],
            "visibility": "hidden_until_match",
            "notification_level": "proposals_only",
        },
    }


async def create_user_nfr(api, subject: str) -> str:  # noqa: ANN001
    """users作成+consent(既存は409許容=べき等)。user idを返す。"""
    body = {
        "display_name": f"ws7-{subject}",
        "birth_date": "1990-01-01",
        "profile": {"bio": "ws-7 NFR試験用"},
    }
    r = await api.post("/v1/users", headers=auth_header(subject), json=body)
    assert r.status_code in (201, 409), f"users {subject}: {r.status_code} {r.text}"
    if r.status_code == 201:
        user_id = r.json()["user"]["id"]
    else:
        r = await api.get("/v1/users/me", headers=auth_header(subject))
        assert r.status_code == 200
        user_id = r.json()["user"]["id"]
    rc = await api.post("/v1/users/me/consent", headers=auth_header(subject))
    assert rc.status_code == 200, f"consent {subject}: {rc.status_code}"
    return user_id


async def create_intent_nfr(api, subject: str, payload: dict) -> str:  # noqa: ANN001
    """POST /v1/intents(active)・intent idを返す。"""
    r = await api.post("/v1/intents", headers=auth_header(subject), json=payload)
    assert r.status_code == 201, f"intents {subject}: {r.status_code} {r.text}"
    return r.json()["intent"]["id"]


async def create_pair_nfr(
    api, db, *, prefix: str, seq: int, start_hours: float = 3.0
) -> dict:  # noqa: ANN001
    """対配置ペア(別ユーザー2人×Intent 2件)を作り収束待ちする。

    stub Jev固定値(compose.staging.yaml env・noul=0.90/score=3.6)で提案が
    成立する条件。ユーザーsubjectはws7-{prefix}a/b・返り値はintent uuidと
    現行version(=1)。
    """
    now = get_clock().now()
    subject_a, subject_b = f"ws7-{prefix}a", f"ws7-{prefix}b"
    await create_user_nfr(api, subject_a)
    await create_user_nfr(api, subject_b)
    intent_a = await create_intent_nfr(
        api, subject_a, intent_payload(seq=seq, now=now, start_hours=start_hours)
    )
    intent_b = await create_intent_nfr(
        api, subject_b, intent_payload(seq=seq + 1, now=now, start_hours=start_hours)
    )
    await wait_pipeline_settled(db)
    return {
        "subject_a": subject_a,
        "subject_b": subject_b,
        "intent_a": intent_a,
        "intent_b": intent_b,
        "version_a": 1,
        "version_b": 1,
    }


async def current_version(db, intent_id: str) -> int:  # noqa: ANN001
    v = await db.fetchval(
        "SELECT version FROM intents WHERE id=$1", uuid.UUID(intent_id)
    )
    assert v is not None, f"intent {intent_id} が見つかりません"
    return int(v)
