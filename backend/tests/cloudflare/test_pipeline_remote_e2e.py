"""実環境マッチングパイプラインE2E(実Workers・D1・Queues・Cron・2026-10-10)。

LATCH_CF_REMOTE=1 + LATCH_CF_API_URL + CLOUDFLARE_API_TOKEN でのみ実行:
  意図2件API作成 → (post-commit配送→queue consumer / cron掃き) →
  LATCH提案自動生成 → 回答yes×2 → matched → cronで通知配送記録、
  までの実測(待ち上限: 提案420秒・通知配送420秒=cron周期*/5の余裕)。

結果は docs/testassets/results/cloudflare-pipeline-e2e-*.yaml へ記録。
LLMはstub(実LLM呼出し無し・費用はWorkers Paid枠内)。
"""

from __future__ import annotations

import json
import os
import sys
import time
import uuid as uuid_mod
from datetime import datetime, timedelta, timezone
from pathlib import Path

import httpx
import pytest
import yaml

BACKEND_DIR = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(BACKEND_DIR / "tests" / "contracts"))

from contracts.cloudflare_harness import WranglerD1  # noqa: E402

pytestmark = pytest.mark.integration

REMOTE = os.environ.get("LATCH_CF_REMOTE") == "1"
BASE_URL = os.environ.get("LATCH_CF_API_URL", "https://latch-api.hiromu1018ks.workers.dev")
RESULTS_DIR = Path(__file__).resolve().parents[3] / "docs" / "testassets" / "results"

PROPOSAL_TIMEOUT_S = 420  # queue即時+cron(*/5)フォールバックの余裕
NOTIFY_TIMEOUT_S = 420


@pytest.fixture(scope="module")
def wrangler() -> WranglerD1:
    return WranglerD1(database="latch-core")


def _d1_scalar(wrangler: WranglerD1, sql: str) -> int:
    out = wrangler.exec(sql)  # execは --json 付きで発行
    data = json.loads(out[out.index("[") :])
    rows = data[0]["results"]
    return int(rows[0]["c"]) if rows else 0


@pytest.mark.skipif(not REMOTE, reason="LATCH_CF_REMOTE=1 のときのみ実環境へ")
async def test_remote_pipeline_end_to_end(wrangler):
    from latch.auth.tools import issue_idp_token

    record: dict = {
        "date": datetime.now(timezone.utc).isoformat(),
        "base_url": BASE_URL,
        "actors": {},
        "timings_s": {},
        "checks": {},
    }
    started = time.monotonic()
    async with httpx.AsyncClient(base_url=BASE_URL, timeout=30.0) as client:
        # -- 2 actor: token→register→consent ---------------------------------
        actors = {}
        for name in ("alpha", "beta"):
            subject = f"pipe-e2e-{name}-{uuid_mod.uuid4().hex[:8]}"
            resp = await client.post(
                "/v1/auth/token",
                json={
                    "provider": "google",
                    "idp_token": issue_idp_token(provider="google", subject=subject),
                },
            )
            assert resp.status_code == 200, resp.text
            headers = {"Authorization": f"Bearer {resp.json()['access_token']}"}
            resp = await client.post(
                "/v1/users",
                headers=headers,
                json={
                    "display_name": f"パイプライン実測-{name}",
                    "birth_date": "1995-06-15",
                    "profile": {},
                },
            )
            assert resp.status_code == 201, resp.text
            user_id = resp.json()["user"]["id"]
            resp = await client.post("/v1/users/me/consent", json={}, headers=headers)
            assert resp.status_code == 200, resp.text
            actors[name] = {"headers": headers, "user_id": user_id}
            record["actors"][name] = {"subject": subject, "user_id": user_id}

        # -- matching意図2件(同時間帯・同地域) --------------------------------
        start = (datetime.now(timezone.utc) + timedelta(hours=6)).isoformat()
        intent_ids: dict[str, str] = {}
        for name in ("alpha", "beta"):
            resp = await client.post(
                "/v1/intents",
                headers=actors[name]["headers"],
                json={
                    "raw_text": "今度の夜、天文館あたりで軽くごはん。",
                    "status": "active",
                    "structured_intent": {
                        "category": {"primary": "meal", "secondary": None},
                        "alcohol_involved": False,
                        "time": {"start": start, "end": None},
                        "location": {"name": "天文館"},
                    },
                },
            )
            assert resp.status_code == 201, resp.text
            intent_ids[name] = resp.json()["intent"]["id"]
        record["intent_ids"] = intent_ids
        record["timings_s"]["intents_created"] = round(time.monotonic() - started, 1)

        # -- 提案の自動生成を待つ(queue即時+cronフォールバック) ----------------
        latch_id = None
        deadline = time.monotonic() + PROPOSAL_TIMEOUT_S
        while time.monotonic() < deadline:
            resp = await client.get(
                "/v1/latches", headers=actors["alpha"]["headers"]
            )
            assert resp.status_code == 200, resp.text
            items = resp.json().get("items", [])
            if items:
                latch_id = items[0]["id"]
                assert items[0]["status"] == "proposed", items[0]
                break
            await _sleep(10)
        assert latch_id is not None, (
            f"{PROPOSAL_TIMEOUT_S}秒以内にLATCH提案が自動生成されなかった"
        )
        record["timings_s"]["proposal_generated"] = round(time.monotonic() - started, 1)
        record["checks"]["proposal_auto_generated"] = True

        # -- 回答 → matched ---------------------------------------------------
        resp = await client.post(
            f"/v1/latches/{latch_id}/response",
            headers=actors["alpha"]["headers"],
            json={"response": "yes"},
        )
        assert resp.status_code == 200, resp.text
        assert resp.json()["latch"]["status"] == "partial_accept", resp.text
        resp = await client.post(
            f"/v1/latches/{latch_id}/response",
            headers=actors["beta"]["headers"],
            json={"response": "yes"},
        )
        assert resp.status_code == 200, resp.text
        assert resp.json()["latch"]["status"] == "matched", resp.text
        record["checks"]["matched"] = True
        record["timings_s"]["matched"] = round(time.monotonic() - started, 1)

        # -- 通知(API面: 両者にproposal+latch_matched) -------------------------
        for name in ("alpha", "beta"):
            resp = await client.get(
                "/v1/notifications", headers=actors[name]["headers"]
            )
            assert resp.status_code == 200, resp.text
            types = sorted(i["type"] for i in resp.json().get("items", []))
            assert types == ["latch_matched", "proposal"], (name, types)
        record["checks"]["notifications_visible"] = True

    # -- cron(*/5)で通知job配送・全job収束をD1面から実測 -----------------------
    deadline = time.monotonic() + NOTIFY_TIMEOUT_S
    delivered = 0
    while time.monotonic() < deadline:
        delivered = _d1_scalar(
            wrangler,
            f"SELECT COUNT(*) AS c FROM notification_deliveries WHERE"
            f" notification_id IN (SELECT id FROM notifications WHERE"
            f" json_extract(payload, '$.latch_id') = '{latch_id}')",
        )
        if delivered >= 4:
            break
        await _sleep(20)
    record["checks"]["notification_delivery_records"] = delivered
    assert delivered == 4, f"通知配送記録4(提案2+成立2)ではない: {delivered}"

    record["d1_state"] = {
        "jobs_unsucceeded": _d1_scalar(
            wrangler,
            "SELECT COUNT(*) AS c FROM jobs WHERE status NOT IN ('succeeded')"
            " AND created_at > 0",
        ),
        "outbox_unsent": _d1_scalar(
            wrangler, "SELECT COUNT(*) AS c FROM outbox WHERE status != 'sent'"
        ),
        "match_candidates_evaluated": _d1_scalar(
            wrangler,
            "SELECT COUNT(*) AS c FROM match_candidates WHERE jev_result"
            " IS NOT NULL",
        ),
        "llm_attempts_jev": _d1_scalar(
            wrangler, "SELECT COUNT(*) AS c FROM llm_attempts WHERE system = 'jev'"
        ),
    }
    record["timings_s"]["total"] = round(time.monotonic() - started, 1)
    assert record["d1_state"]["llm_attempts_jev"] >= 1
    record["worker_probe_events"] = _last_scheduled(wrangler)

    out = RESULTS_DIR / (
        "cloudflare-pipeline-e2e-"
        + datetime.now(timezone.utc).strftime("%Y%m%d-%H%M%S")
        + ".yaml"
    )
    RESULTS_DIR.mkdir(parents=True, exist_ok=True)
    out.write_text(yaml.safe_dump(record, allow_unicode=True, sort_keys=False))
    print(f"\nwrote {out}")


def _last_scheduled(wrangler: WranglerD1) -> dict | None:
    """直近cron実行のoutcome(probe_events・稼働証跡)。"""
    out = wrangler.exec(
        "SELECT payload, created_at FROM probe_events WHERE kind = 'scheduled'"
        " ORDER BY id DESC LIMIT 1"
    )
    data = json.loads(out[out.index("[") :])
    rows = data[0]["results"]
    if not rows:
        return None
    return {
        "created_at": rows[0]["created_at"],
        "payload": json.loads(rows[0]["payload"]),
    }


async def _sleep(seconds: float) -> None:
    import asyncio

    await asyncio.sleep(seconds)
