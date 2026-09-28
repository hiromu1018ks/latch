"""Embedding実APIスモーク(make embed-smoke・design §2.7)。

ws-6の401事故の教訓: 実APIを叩くまで分からない契約ズレの検出。1呼び出し
(約$0.000015・約100トークン)。make embed-smoke は uv run --env-file ../.env
経由で LATCH_LLM_MODE=real と LATCH_GEMINI_API_KEY を渡す(g1-gateと同じ経路)。
SendRecordも通常どおり出力される(Gateway経由 — 08 §3)。レイテンシ計測は
asyncioのloop.time(処理時間の測定であり時刻参照ではない — arch test対象外)。
"""

from __future__ import annotations

import asyncio

from latch.core.clock import SystemClock
from latch.llm.gateway import build_embedding_gateway
from latch.llm.gemini import GEMINI_EMBEDDING_MODEL
from latch.llm.providers import EMBEDDING_DIMENSIONS
from latch.settings import Settings

# 07 §3形式の固定サンプル(設計§2.4の例と同じ要素)
SAMPLE_TEXT = "drinking / 平日夜20-23時 / 天文館 / 2-4人 / 静かなお店で・予算は抑えめ"


async def main() -> int:
    settings = Settings()
    if settings.llm_mode != "real":
        print("[embed-smoke] FAIL: LATCH_LLM_MODE=real が必要(.env・make embed-smoke)")
        return 1
    if not settings.llm_gemini_api_key:
        print("[embed-smoke] FAIL: LATCH_GEMINI_API_KEY が未設定(.env)")
        return 1
    gateway = build_embedding_gateway(SystemClock(), settings)  # 鍵欠落はfail-fast
    loop = asyncio.get_running_loop()
    started = loop.time()
    vec = await gateway.embed_intent(text=SAMPLE_TEXT, intent_id="embed-smoke")
    elapsed_ms = (loop.time() - started) * 1000
    head = [round(v, 4) for v in vec[:3]]
    print(
        f"[embed-smoke] model={GEMINI_EMBEDDING_MODEL}"
        f" dim={len(vec)} latency_ms={elapsed_ms:.0f} head={head}"
    )
    if len(vec) != EMBEDDING_DIMENSIONS:
        print(f"[embed-smoke] FAIL: dimensions != {EMBEDDING_DIMENSIONS}")
        return 1
    print("[embed-smoke] OK")
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
