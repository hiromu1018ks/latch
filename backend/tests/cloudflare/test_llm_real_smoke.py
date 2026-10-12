"""実LLM切替スモーク(実配備Workers・実Anthropic/Gemini/TypeSafe・2026-10-12)。

LATCH_CF_REMOTE=1 + LATCH_CF_REAL_SMOKE=1 + CLOUDFLARE_API_TOKEN でのみ実行
(実課金を伴うため二重ゲート)。前提: 実配備でwrangler secret
LATCH_LLM_MODE=real(鍵3本もsecret設定済み)であること。

確認すること(A-2完了条件・接続・記録・コスト):
  1. parse 1件が実Anthropicで動く(応答+llm_attempts system='parser')
  2. 意図2件→実パイプラインでembedding(実Gemini・llm_attempts
     system='embedding')・Jev(実TypeSafe〔フォールバック実Anthropic〕・
     llm_attempts system='jev'・match_candidates.jev_resultのprovider/model)
  3. 提案→yes×2→matchedまで実挙動
  4. 観測されたtoken数・呼び出し回数の記録(費用の見積り材料)

結果は docs/testassets/results/llm-real-smoke-<日時>.yaml へ。
品質の再評価は行わない(G1/G2ゲートで実API検証済み・目的は接続・記録・コスト)。
"""

from __future__ import annotations

import json
import os
import sys
import time
import uuid as uuid_mod
from datetime import UTC, date, datetime, timedelta
from pathlib import Path

import httpx
import pytest
import yaml

BACKEND_DIR = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(BACKEND_DIR / "tests" / "contracts"))

from contracts.cloudflare_harness import WranglerD1  # noqa: E402

pytestmark = pytest.mark.integration

REMOTE = os.environ.get("LATCH_CF_REMOTE") == "1"
REAL = os.environ.get("LATCH_CF_REAL_SMOKE") == "1"
BASE_URL = os.environ.get(
    "LATCH_CF_API_URL", "https://latch-api.hiromu1018ks.workers.dev"
)
RESULTS_DIR = Path(__file__).resolve().parents[3] / "docs" / "testassets" / "results"

PROPOSAL_TIMEOUT_S = 420  # queue即時+cron(*/5)フォールバックの余裕

PARSE_TEXT = (
    "明日の夜、天文館近くで海鮮系の食事に行きたい。予算は8000円までで"
    "2人くらいで静かなお店がいい。"
)

# 見積り(スモーク実行前に提示するものと同値・YAML metaへ記録):
#   parse 1×Anthropic Haiku 4.5 + embedding 2×gemini-embedding-001
#   + jev 1〜2×TypeSafe(フォールバック時+Anthropic Haiku)
#   → 数十呼び出し未満・$0.1以下の見込み
COST_ESTIMATE = {
    "planned_calls": {
        "parser_anthropic_haiku45": 1,
        "embedding_gemini": 2,
        "jev_typesafe": 1,
        "jev_fallback_anthropic_haiku": "0〜1(429/529/timeout時のみ)",
    },
    "estimate_usd_upper": 0.1,
}


@pytest.fixture(scope="module")
def wrangler() -> WranglerD1:
    return WranglerD1(database="latch-core")


def _rows(wrangler: WranglerD1, sql: str) -> list[dict]:
    out = wrangler.exec(sql)
    data = json.loads(out[out.index("[") :])
    return data[0]["results"]


async def _sleep(seconds: float) -> None:
    import asyncio

    await asyncio.sleep(seconds)


async def _run_smoke_body(client, actors, intent_ids, record, started) -> None:
    """提案待ち→yes×2→matched(失敗時は呼び出し元がIntent取消で後始末)。"""
    latch_id = None
    deadline = time.monotonic() + PROPOSAL_TIMEOUT_S
    while time.monotonic() < deadline:
        resp = await client.get("/v1/latches", headers=actors["alpha"]["headers"])
        assert resp.status_code == 200, resp.text
        items = resp.json().get("items", [])
        if items:
            latch_id = items[0]["id"]
            assert items[0]["status"] == "proposed", items[0]
            break
        await _sleep(10)
    assert latch_id is not None, (
        f"{PROPOSAL_TIMEOUT_S}秒以内に実Jev経由の提案が生成されなかった"
    )
    record["timings_s"]["proposal_generated"] = round(time.monotonic() - started, 1)
    record["checks"]["proposal_auto_generated"] = True

    # -- yes×2 → matched ---------------------------------------------------
    resp = await client.post(
        f"/v1/latches/{latch_id}/response",
        headers=actors["alpha"]["headers"],
        json={"response": "yes"},
    )
    assert resp.status_code == 200, resp.text
    resp = await client.post(
        f"/v1/latches/{latch_id}/response",
        headers=actors["beta"]["headers"],
        json={"response": "yes"},
    )
    assert resp.status_code == 200, resp.text
    assert resp.json()["latch"]["status"] == "matched", resp.text
    record["checks"]["matched"] = True
    record["timings_s"]["matched"] = round(time.monotonic() - started, 1)


@pytest.mark.skipif(
    not (REMOTE and REAL), reason="LATCH_CF_REMOTE=1 + LATCH_CF_REAL_SMOKE=1 のみ"
)
async def test_llm_real_smoke(wrangler):
    from latch.auth.tools import issue_idp_token

    record: dict = {
        "date": datetime.now(UTC).isoformat(),
        "base_url": BASE_URL,
        "llm_mode": "real",
        "mechanism": "wrangler.jsoncからLATCH_LLM_MODE varを削除(同名secretの"
        "登録をwranglerが拒否するため・code 10053)+wrangler secret "
        "LATCH_LLM_MODE=real。コード既定値がstubのためsecret未設定の"
        "ローカルdev/miniflareは常にstub",
        "cost_estimate": COST_ESTIMATE,
        "actors": {},
        "timings_s": {},
        "checks": {},
    }
    start_us = int(time.time() * 1e6) - 5_000_000  # 直前バッファ5秒
    started = time.monotonic()
    async with httpx.AsyncClient(base_url=BASE_URL, timeout=30.0) as client:
        # -- actor 2名: token→register→consent --------------------------------
        actors = {}
        for name in ("alpha", "beta"):
            subject = f"llm-smoke-{name}-{uuid_mod.uuid4().hex[:8]}"
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
                    "display_name": f"実LLMスモーク-{name}",
                    "birth_date": "1995-06-15",
                    "profile": {},
                },
            )
            assert resp.status_code == 201, resp.text
            user_id = resp.json()["user"]["id"]
            resp = await client.post(
                "/v1/users/me/consent", json={}, headers=headers
            )
            assert resp.status_code == 200, resp.text
            actors[name] = {"headers": headers, "user_id": user_id}
            record["actors"][name] = {"subject": subject, "user_id": user_id}

        # -- 1. parse 1件(実Anthropic) ----------------------------------------
        resp = await client.post(
            "/v1/intents/parse", json={"text": PARSE_TEXT},
            headers=actors["alpha"]["headers"],
        )
        assert resp.status_code == 200, resp.text
        parsed = resp.json()["structured_intent"]
        record["parse"] = {
            "input_text_head": PARSE_TEXT[:24],
            "category_primary": parsed.get("category", {}).get("primary"),
            "time_start": parsed.get("time", {}).get("start"),
            "location_name": parsed.get("location", {}).get("name"),
            "budget_max": (parsed.get("budget") or {}).get("max"),
            "alcohol_involved": parsed.get("alcohol_involved"),
        }
        # stub決定出力との差分(実呼び出しの副次証拠・主証拠はllm_attempts)
        from latch.llm.stub import StubLLM

        stub_out = await StubLLM().complete_structured(PARSE_TEXT, date.today())
        record["parse"]["differs_from_stub"] = (
            json.dumps(parsed, sort_keys=True, default=str)
            != json.dumps(stub_out, sort_keys=True, default=str)
        )
        record["timings_s"]["parse_done"] = round(time.monotonic() - started, 1)

        # -- 2. 意図2件(同時間帯・同地域。スロットは実行ごとに分離[A-01規律]:
        #    過去runの同テキストIntentと交差マッチすると非参加者のlatchへ
        #    403が出る。+7h台の分オフセットで排他) ---------------------------
        import random

        slot = datetime.now(UTC) + timedelta(hours=7, minutes=random.randrange(0, 40))
        start = slot.isoformat()
        intent_ids: dict[str, str] = {}
        for name in ("alpha", "beta"):
            resp = await client.post(
                "/v1/intents",
                headers=actors[name]["headers"],
                json={
                    "raw_text": PARSE_TEXT,
                    "status": "active",
                    "structured_intent": {
                        "category": {"primary": "meal", "secondary": "seafood"},
                        "alcohol_involved": False,
                        "time": {"start": start, "end": None},
                        "location": {"name": "天文館"},
                        "budget": {"max": 8000},
                    },
                },
            )
            assert resp.status_code == 201, resp.text
            intent_ids[name] = resp.json()["intent"]["id"]
        record["intent_ids"] = intent_ids
        record["timings_s"]["intents_created"] = round(time.monotonic() - started, 1)

        # 以降の失敗でIntentを活性のまま残さない(交差マッチ防止[A-01規律])
        try:
            await _run_smoke_body(client, actors, intent_ids, record, started)
        except BaseException:
            for iid in intent_ids.values():
                for name in ("alpha", "beta"):
                    try:
                        await client.delete(
                            f"/v1/intents/{iid}",
                            headers=actors[name]["headers"],
                        )
                    except Exception:  # noqa: BLE001 — 後始末のベストエフォート
                        pass
            raise

    # -- D1面: llm_attempts(parser/embedding/jev)とjev_resultのprovider ------
    window = f"created_at >= {start_us}"
    attempts = _rows(
        wrangler,
        "SELECT system, status, actual_provider, actual_model,"
        " SUM(input_tokens) AS in_tok, SUM(output_tokens) AS out_tok,"
        " COUNT(*) AS c FROM llm_attempts"
        f" WHERE {window} GROUP BY system, status, actual_provider, actual_model",
    )
    record["llm_attempts_window"] = attempts

    def _attempt(system: str) -> list[dict]:
        return [r for r in attempts if r["system"] == system]

    parser_rows = _attempt("parser")
    embedding_rows = _attempt("embedding")
    jev_rows = _attempt("jev")
    assert any(
        r["actual_provider"] == "anthropic" and r["status"] == "succeeded"
        for r in parser_rows
    ), f"parser試行が実Anthropicとして記録されていない: {parser_rows}"
    assert any(
        r["actual_provider"] == "google" and r["status"] == "succeeded"
        for r in embedding_rows
    ), f"embedding試行が実Geminiとして記録されていない: {embedding_rows}"
    assert any(
        r["actual_provider"] in ("typesafe_jev", "fallback_llm")
        and r["status"] == "succeeded"
        for r in jev_rows
    ), f"jev試行が実providerとして記録されていない: {jev_rows}"
    record["checks"]["llm_attempts_real_providers"] = {
        "parser": "anthropic",
        "embedding": "google",
        "jev": sorted({r["actual_provider"] for r in jev_rows if r["actual_provider"]}),
    }

    jev_result_rows = _rows(
        wrangler,
        f"SELECT jev_result, latch_score FROM match_candidates WHERE"
        f" (intent_a_id = '{intent_ids['alpha']}' AND"
        f"  intent_b_id = '{intent_ids['beta']}')"
        f" OR (intent_a_id = '{intent_ids['beta']}' AND"
        f"     intent_b_id = '{intent_ids['alpha']}')",
    )
    assert jev_result_rows, "当該ペアのmatch_candidates行がない"
    jev_payload = json.loads(jev_result_rows[0]["jev_result"])
    assert jev_payload["provider"] in ("typesafe_jev", "fallback_llm"), jev_payload
    record["jev_result"] = {
        "provider": jev_payload["provider"],
        "model": jev_payload.get("model"),
        "would_a_accept_b": jev_payload["would_a_accept_b"],
        "would_b_accept_a": jev_payload["would_b_accept_a"],
        "latch_score": jev_result_rows[0]["latch_score"],
    }
    record["checks"]["jev_result_provider_recorded"] = True
    record["timings_s"]["total"] = round(time.monotonic() - started, 1)

    out = RESULTS_DIR / (
        "llm-real-smoke-" + datetime.now(UTC).strftime("%Y%m%d-%H%M%S") + ".yaml"
    )
    RESULTS_DIR.mkdir(parents=True, exist_ok=True)
    out.write_text(yaml.safe_dump(record, allow_unicode=True, sort_keys=False))
    print(f"\nwrote {out}")
