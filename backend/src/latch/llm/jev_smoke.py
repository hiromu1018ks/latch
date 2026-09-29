"""Jev実APIスモーク(make jev-smoke・design §3.1)。実行はスーパーバイザー検証時のみ。

07 §4例の正規化テキスト2件を固定入力とし、第一候補(TypeSafe Jev)を1呼び出し
する。SendRecordも通常どおり出力される(Gateway経由 — 08 §3)。実行には
.env の LATCH_LLM_MODE=real・LATCH_GEMINI_API_KEY・LATCH_TYPESAFE_API_KEY・
LATCH_ANTHROPIC_API_KEY が必要(make jev-smoke が uv run --env-file ../.env 経由)。
FALLBACK=1 でフォールバックLLM(Anthropic Sonnet 5)の直接呼び出しに切り替える
(第一候補の障害を再現せず、フォールバック経路単体の応答確認用)。
noul応答のJSON形状の最終確認(design §5-7)もここで行う。
"""

from __future__ import annotations

import asyncio
import json
from datetime import datetime

from latch.core.clock import SystemClock
from latch.llm.gateway import build_worker_gateway
from latch.llm.jev import JEV_MODEL, JevTextInput, build_jev_text, validate_and_normalize
from latch.settings import Settings


def _from_iso(s: str) -> datetime:
    return datetime.fromisoformat(s)


# 07 §4の正規化テキスト例をそのまま固定入力にする
A = JevTextInput(
    category_primary="drinking",
    structured_data={
        "location_name": "天文館周辺",
        "soft_constraints": [
            {"text": "軽く飲みたい", "downgraded_from_ng": False},
            {"text": "会社関係の人は避けたい", "downgraded_from_ng": True},
        ],
    },
    participants_min=2,
    participants_max=4,
    time_start=_from_iso("2026-09-26T20:00:00+09:00"),
    time_end=_from_iso("2026-09-26T23:00:00+09:00"),
    budget_max=5000,
    geo_radius_m=2000,
)
B = JevTextInput(
    category_primary="meal",
    structured_data={"location_name": "天文館周辺", "soft_constraints": []},
    participants_min=2,
    participants_max=2,
    time_start=_from_iso("2026-09-26T19:30:00+09:00"),
    time_end=_from_iso("2026-09-26T22:30:00+09:00"),
    budget_max=4000,
    geo_radius_m=1000,
)


async def main() -> int:
    import os

    settings = Settings()
    if settings.llm_mode != "real":
        print("[jev-smoke] FAIL: LATCH_LLM_MODE=real が必要(.env・make jev-smoke)")
        return 1
    if not settings.llm_typesafe_api_key:
        print("[jev-smoke] FAIL: LATCH_TYPESAFE_API_KEY が未設定(.env)")
        return 1
    if not settings.llm_anthropic_api_key:
        print("[jev-smoke] FAIL: LATCH_ANTHROPIC_API_KEY が未設定(.env・フォールバック用)")
        return 1
    if not settings.llm_gemini_api_key:
        print("[jev-smoke] FAIL: LATCH_GEMINI_API_KEY が未設定(.env・realは3鍵必須)")
        return 1
    gateway = build_worker_gateway(SystemClock(), settings)  # 鍵欠落はfail-fast
    text_a = build_jev_text(A, label="Intent A")
    text_b = build_jev_text(B, label="Intent B")
    if os.environ.get("FALLBACK") == "1":
        envelope = await gateway._jev_fallback.judge(text_a, text_b)
        provider, model = "fallback_llm(直接)", envelope.get("model")
        result = validate_and_normalize(envelope)
    else:
        judgment = await gateway.judge_pair(
            intent_a=text_a, intent_b=text_b, intent_ids=["jev-smoke-a", "jev-smoke-b"]
        )
        provider, model, result = judgment.provider, judgment.model, judgment.result
    print("[jev-smoke] provider=", provider, " model=", model)
    print("[jev-smoke] result=", json.dumps(result, ensure_ascii=False))
    print("[jev-smoke] expected_model=", JEV_MODEL)
    print("[jev-smoke] OK")
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
