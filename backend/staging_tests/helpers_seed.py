"""シード投入と突合基盤(M4 ws-3・引用確定値#12・ws-2引継ぎ)。

投入順序はusers→consent→intents(ws-5実装後は同意なしで422 CONSENT_REQUIRED
になるためconsentを先に済ませる)。intended_reject_cases(EX-029〜033)は
投入しない — S5で422試行素材として使う。reference_l1の判定はYAML文字列を
内部表現(birth_date=date・time=datetime)へ変換してから行う(verify.py同型)。
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

import yaml

from conftest import SEED_PATH, auth_header, ensure_env_prerequisites
from latch.testassets.reference_l1 import judge_all

JST = timezone(timedelta(hours=9))

EX_PASS_SAMPLE = (
    "EX-006",
    "EX-008",
    "EX-010",
    "EX-013",
    "EX-015",
    "EX-017",
    "EX-020",  # 設計§2.5 #6の材料
)

_SEED_STATE: dict | None = None


def _p(value: str) -> datetime:
    return datetime.fromisoformat(value)


def load_seed() -> dict:
    return yaml.safe_load(SEED_PATH.read_text(encoding="utf-8"))


def to_reference_view(seed: dict) -> tuple[dict, dict]:
    """reference_l1用の内部表現(birth_date=date・time=datetimeへ変換)。"""
    users = {
        u["id"]: {**u, "birth_date": _p(u["birth_date"]).date()} for u in seed["users"]
    }
    intents = []
    for i in seed["intents"]:
        t = i["structured"]["time"]
        intents.append(
            {
                "id": i["id"],
                "user": i["user"],
                "structured": {
                    **i["structured"],
                    "time": {
                        "start": _p(t["start"]),
                        "end": _p(t["end"]) if t.get("end") else None,
                    },
                },
            }
        )
    return users, intents


def _seed_intent_payload(i: dict) -> dict:
    """POST /v1/intentsのbody(05 §5・structured_intent明示・引用確定値#13)。"""
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


async def _n_created_processed(db) -> int:  # noqa: ANN001
    return await db.fetchval(
        "SELECT count(*) FROM match_events"
        " WHERE event_type='created' AND status='processed'"
    )


async def _evaluated_count(db) -> int:  # noqa: ANN001
    return await db.fetchval(
        "SELECT count(*) FROM match_candidates WHERE jev_result IS NOT NULL"
    )


async def wait_pipeline_settled(db) -> None:  # noqa: ANN001
    """created Event 300件処理+Layer1〜5の収束待ち(設計§2.3・上限180秒+安定確認)。"""
    import asyncio
    from time import monotonic

    n = await _n_created_processed(db)
    assert n == 300, f"created processedが300でない: {n}(投入直後でなければリセット)"

    # 安定確認(評価行数が10秒間隔で2回連続不変になるまで・上限90秒)
    prev = await _evaluated_count(db)
    deadline = monotonic() + 90
    while True:
        if monotonic() > deadline:
            raise AssertionError("タイムアウト(90s): match_candidates評価行の安定")
        await asyncio.sleep(10)
        cur = await _evaluated_count(db)
        if cur == prev:
            return
        prev = cur


async def seed_all(api, db) -> dict:  # noqa: ANN001
    """シード投入(users→consent→intents)+収束待ち。2回目以降はキャッシュ。"""
    global _SEED_STATE
    if _SEED_STATE is not None:
        return _SEED_STATE

    await ensure_env_prerequisites(db)
    seed = load_seed()
    meta_base = seed["meta"]["base_datetime"]

    su_to_uuid: dict[str, str] = {}
    users_ok = 0
    consent_ok = 0
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
        su_to_uuid[u["id"]] = r.json()["user"]["id"]
        users_ok += 1
        # ws-5実装後は同意なしだとIntentが422 CONSENT_REQUIREDで遮断される
        # ため全員を同意済みにする(スーパーバイザー引継ぎ2・期待値表は不変)
        rc = await api.post("/v1/users/me/consent", headers=auth_header(u["id"]))
        assert rc.status_code == 200, (
            f"consent失敗 {u['id']}: {rc.status_code} {rc.text}"
        )
        consent_ok += 1

    sd_to_uuid: dict[str, str] = {}
    intents_ok = 0
    for i in seed["intents"]:
        r = await api.post(
            "/v1/intents",
            headers=auth_header(i["user"]),
            json=_seed_intent_payload(i),
        )
        assert r.status_code == 201, (
            f"intents投入失敗 {i['id']}: {r.status_code} {r.text}"
        )
        sd_to_uuid[i["id"]] = r.json()["intent"]["id"]
        intents_ok += 1

    await wait_pipeline_settled(db)

    # reference_l1の全ペア判定(now=base_datetime・ws-2の突合基準)
    users, intents = to_reference_view(seed)
    judged = judge_all(intents, users, now=_p(meta_base))
    l1_pass = {k for k, (ok, _reason) in judged.items() if ok}

    # D-08事前集計(設計§2.2対抗策(c)・計画時実測: 6件超え74件)
    intent_user = {i["id"]: i["user"] for i in intents}
    per_user_day: dict[tuple[str, str], int] = {}
    for a, b in l1_pass:
        day = (
            next(i for i in intents if i["id"] == a)["structured"]["time"]["start"]
            .date()
            .isoformat()
        )
        for iid in (a, b):
            key = (intent_user[iid], day)
            per_user_day[key] = per_user_day.get(key, 0) + 1

    _SEED_STATE = {
        "meta_base": meta_base,
        "su_to_uuid": su_to_uuid,
        "sd_to_uuid": sd_to_uuid,
        "l1_pass": l1_pass,
        "ex_pass_sample": set(EX_PASS_SAMPLE),
        "pass_pairs_per_user_day": per_user_day,
        "users_ok": users_ok,
        "consent_ok": consent_ok,
        "intents_ok": intents_ok,
    }
    return _SEED_STATE


async def fetch_candidates_pairs(
    db, sd_to_uuid: dict[str, str]
) -> set[tuple[str, str]]:  # noqa: ANN001
    """DB match_candidatesの無向化集合(シードペアのみ・設計§2.2対称性注記)。"""
    uuid_to_sd = {v: k for k, v in sd_to_uuid.items()}
    rows = await db.fetch("SELECT intent_a_id, intent_b_id FROM match_candidates")
    result: set[tuple[str, str]] = set()
    for r in rows:
        a = uuid_to_sd.get(str(r["intent_a_id"]))
        b = uuid_to_sd.get(str(r["intent_b_id"]))
        if a is None or b is None:
            continue  # 専用シナリオペアはシード突合の対象外
        result.add(tuple(sorted((a, b))))
    return result


def ex_pair_ids(seed: dict, ex_id: str) -> tuple[str, ...] | None:
    """expectations.casesからEX-xxxのintents(idタプル・ソート済み)を引く。"""
    for case in seed["expectations"]["cases"]:
        if case["id"] == ex_id:
            return tuple(sorted(case["intents"]))
    return None


async def compute_mlr(db) -> dict:  # noqa: ANN001
    """MLR集計(01 §23・09 §2.1): 分母=proposed遷移数(1候補1・グループも1・
    期限切れ/NO/競合クローズも除かない)・分子=matched遷移数(全員YES)。
    mutedもproposed遷移のため分母に含まれる(06 §2)。"""
    denominator = await db.fetchval(
        "SELECT count(*) FROM latch_status_events WHERE to_status='proposed'"
    )
    numerator = await db.fetchval(
        "SELECT count(*) FROM latch_status_events WHERE to_status='matched'"
    )
    return {
        "denominator": denominator,
        "numerator": numerator,
        "mlr": (numerator / denominator) if denominator else None,
    }
