"""実LLM呼び出しのWorkers実装(fetch直書きtransport・2026-10-12)。

LATCH_LLM_MODE=realのときparse・pipelineが使う実プロバイダ群。SDK
(anthropic・google-genai・typesafe_sdk)はバンドル上限(gzip 10MB)のため
Workersに入らない — 移行計画§6「LLM: fetch直書きtransport(stubで開始)」の
real側実装である。プロトコルは本体SDK実装(anthropic.py・gemini.py・
typesafe.py・anthropic_jev.py)と同じ値(model・max_tokens・schema後加工・
質問定数)を使い、差はtransportのみ:

- Parser(Anthropic Messages・claude-haiku-4-5・structured outputs・1024)
- Embedding(Gemini gemini-embedding-001・768次元・outputDimensionality指定)
- Jev第一候補(TypeSafe system one POST /v1/systemone・JEV_QUESTIONS素通り)
- Jevフォールバック(Anthropic Haiku・FALLBACK_SYSTEM_PROMPT・thinking無効)

timeout・フォールバック・breaker・送信記録の規律は本体LLMGatewayをそのまま
再利用する(build_real_gateway・ProtocolはGatewayと適合)。鍵はwrangler
secret(LATCH_ANTHROPIC_API_KEY等)からenv経由で渡す。real構成でのみ
llm_attemptsへparser/embeddingの試行も記録する(jevはEvaluationJobRunner
が従来どおり記録・stub経路の記録は一切変えない)。
"""

from __future__ import annotations

import json
from datetime import date
from typing import Any

from latch.llm.anthropic import (
    ANTHROPIC_PARSER_MAX_TOKENS,
    ANTHROPIC_PARSER_MODEL,
    adapt_schema_for_anthropic,
)
from latch.llm.anthropic_jev import (
    ANTHROPIC_JEV_FALLBACK_MAX_TOKENS,
    ANTHROPIC_JEV_FALLBACK_MODEL,
    AnthropicJevFallbackProvider,
    FALLBACK_SYSTEM_PROMPT,
    FallbackJevAnswers,
)
from latch.llm.errors import (
    LLMConnectionError,
    LLMOverloadedError,
    LLMProviderError,
    LLMRateLimitError,
)
from latch.llm.gemini import GEMINI_EMBEDDING_MODEL
from latch.llm.jev import JEV_MODEL, JEV_QUESTIONS, JevJudgment
from latch.llm.providers import (
    EMBEDDING_DIMENSIONS,
    EmbeddingProvider,
    JevProvider,
    ParserProvider,
)
from latch.llm.typesafe import TYPESAFE_JEV_BASE_URL

ANTHROPIC_BASE_URL = "https://api.anthropic.com"
GEMINI_BASE_URL = "https://generativelanguage.googleapis.com"
ANTHROPIC_VERSION = "2023-06-01"  # Messages APIの必須バージョンヘッダー
PRICE_VERSION_REAL = "real-1"  # pipelineのPRICE_VERSION(stub-1)と区別


def _env_var(env, name: str, default: str = "") -> str:
    value = getattr(env, name, None)
    return str(value) if value is not None else default


# ==============================================================================
# transport(workers.fetch・試験はset_transportで差し替え)
# ==============================================================================

_transport = None  # 試験注入用(async (url, headers, body) -> (status, text))


def set_transport(fn) -> None:
    """単体試験用のtransport差し替え(CPythonではworkers.fetchをimportできない)。"""
    global _transport
    _transport = fn


async def _post_json(url: str, *, headers: dict, body: dict) -> dict:
    """fetchでJSONをPOSTし、2xxならJSON dictを返す。失敗はGatewayの切替条件
    (design §2.2)に適合するLLMError系へ写像する(429/529/接続/その他)。"""
    try:
        if _transport is not None:
            status, text = await _transport(url, headers=headers, body=body)
        else:
            from workers.fetch import fetch  # Pyodideのみ(本番Worker経路)

            resp = await fetch(
                url,
                method="POST",
                headers=headers,
                body=json.dumps(body, ensure_ascii=False),
            )
            status = int(resp.status)
            text = await resp.text()
    except OSError as exc:
        # pyfetchはJsExceptionをOSErrorへ包む(ネットワーク層の失敗)
        raise LLMConnectionError("workers fetch failed") from exc
    if status == 429:
        raise LLMRateLimitError(f"provider status {status}")
    if status == 529:
        raise LLMOverloadedError(f"provider status {status}")
    if not 200 <= status < 300:
        # 本文は種類のみ添える(原文を例外へ載せない・08 §2.4と同様の規律)
        raise LLMProviderError(f"provider status {status} ({len(text)} bytes)")
    try:
        return json.loads(text)
    except ValueError as exc:
        raise LLMProviderError("provider returned non-JSON body") from exc


def _anthropic_headers(api_key: str) -> dict:
    return {
        "x-api-key": api_key,
        "anthropic-version": ANTHROPIC_VERSION,
        "content-type": "application/json",
    }


async def _anthropic_text_blocks(data: dict) -> str:
    """structured outputsの保証(最初のtextブロックがvalid JSON)から取り出す。"""
    for block in data.get("content") or []:
        if isinstance(block, dict) and block.get("type") == "text":
            return str(block.get("text") or "")
    raise LLMProviderError("anthropic response had no text block")


# ==============================================================================
# 系統別プロバイダ(Protocolは本体SDK実装と同一・値も同一)
# ==============================================================================


class FetchAnthropicParser(ParserProvider):
    """Parser系統(実Anthropic・Messages直書き)。name="anthropic"(08 §3)。"""

    def __init__(
        self,
        *,
        api_key: str,
        system_prompt: str,
        output_schema: dict,
        base_url: str = ANTHROPIC_BASE_URL,
    ) -> None:
        if not api_key:
            raise ValueError("FetchAnthropicParser requires api_key")  # fail-fast
        self.name = "anthropic"
        self._api_key = api_key
        self._base = base_url.rstrip("/")
        self._system_prompt = system_prompt
        self._schema = adapt_schema_for_anthropic(output_schema)

    async def complete_structured(self, text: str, current_date: date) -> dict:
        data = await _post_json(
            f"{self._base}/v1/messages",
            headers=_anthropic_headers(self._api_key),
            body={
                "model": ANTHROPIC_PARSER_MODEL,
                "system": self._system_prompt.replace(
                    "{current_date}", current_date.isoformat()
                ),
                "messages": [{"role": "user", "content": text}],
                "max_tokens": ANTHROPIC_PARSER_MAX_TOKENS,
                "output_config": {
                    "format": {"type": "json_schema", "schema": self._schema}
                },
            },
        )
        first = await _anthropic_text_blocks(data)
        return json.loads(first)


class FetchGeminiEmbedding(EmbeddingProvider):
    """Embedding系統(実Gemini・embedContent直書き)。name="google"(08 §3)。"""

    def __init__(self, *, api_key: str, base_url: str = GEMINI_BASE_URL) -> None:
        if not api_key:
            raise ValueError("FetchGeminiEmbedding requires api_key")  # fail-fast
        self.name = "google"
        self._api_key = api_key
        self._base = base_url.rstrip("/")

    async def embed(self, text: str) -> list[float]:
        data = await _post_json(
            f"{self._base}/v1beta/models/{GEMINI_EMBEDDING_MODEL}:embedContent",
            headers={
                "x-goog-api-key": self._api_key,
                "content-type": "application/json",
            },
            body={
                "content": {"parts": [{"text": text}]},
                "outputDimensionality": EMBEDDING_DIMENSIONS,
            },
        )
        embedding = data.get("embedding") or {}
        values = embedding.get("values") or []
        if len(values) != EMBEDDING_DIMENSIONS:
            raise LLMProviderError(
                f"embedding dimension mismatch: {len(values)} != {EMBEDDING_DIMENSIONS}"
            )
        return [float(v) for v in values]


class FetchTypeSafeJev(JevProvider):
    """Jev第一候補(実TypeSafe・system one直書き)。name="typesafe"(08 §3)。

    wire形式はtypesafe_sdkのendpoints.prepare_system_oneと同一
    (state・model・questions。questionsはJEV_QUESTIONS定数が単一の真実)。
    """

    def __init__(self, *, api_key: str, base_url: str = TYPESAFE_JEV_BASE_URL) -> None:
        if not api_key:
            raise ValueError("FetchTypeSafeJev requires api_key")  # fail-fast
        self.name = "typesafe"
        self._api_key = api_key
        self._base = base_url.rstrip("/")

    async def judge(self, intent_a: str, intent_b: str) -> dict:
        data = await _post_json(
            f"{self._base}/v1/systemone",
            headers={
                "authorization": f"Bearer {self._api_key}",
                "accept": "application/json",
                "content-type": "application/json",
            },
            body={
                "state": {"intent_a": intent_a, "intent_b": intent_b},
                "model": JEV_MODEL,
                "questions": JEV_QUESTIONS,
            },
        )
        usage = data.get("usage") or {}
        return {
            "model": data.get("model"),
            "answers": dict(data.get("answers") or {}),
            "usage": {
                "input_tokens": usage.get("input_tokens"),
                "output_tokens": usage.get("output_tokens"),
            },
        }


class FetchAnthropicJevFallback(JevProvider):
    """Jevフォールバック(実Anthropic Haiku)。name="anthropic"(08 §3)。

    プロンプト・スキーマ・answers変換はanthropic_jev.pyの定義をそのまま
    importして使う(手書き複製なし・modelは引用#8どおりnull)。
    """

    def __init__(self, *, api_key: str, base_url: str = ANTHROPIC_BASE_URL) -> None:
        if not api_key:
            raise ValueError("FetchAnthropicJevFallback requires api_key")  # fail-fast
        self.name = "anthropic"
        self._api_key = api_key
        self._base = base_url.rstrip("/")
        self._schema = adapt_schema_for_anthropic(FallbackJevAnswers.model_json_schema())

    async def judge(self, intent_a: str, intent_b: str) -> dict:
        data = await _post_json(
            f"{self._base}/v1/messages",
            headers=_anthropic_headers(self._api_key),
            body={
                "model": ANTHROPIC_JEV_FALLBACK_MODEL,
                "system": FALLBACK_SYSTEM_PROMPT,
                "messages": [{"role": "user", "content": f"{intent_a}\n\n{intent_b}"}],
                "output_config": {
                    "format": {"type": "json_schema", "schema": self._schema}
                },
                "thinking": {"type": "disabled"},
                "max_tokens": ANTHROPIC_JEV_FALLBACK_MAX_TOKENS,
            },
        )
        first = await _anthropic_text_blocks(data)
        parsed = json.loads(first)
        usage = data.get("usage") or {}
        return {
            "model": None,
            "answers": AnthropicJevFallbackProvider._answers(parsed),
            "usage": {
                "input_tokens": usage.get("input_tokens"),
                "output_tokens": usage.get("output_tokens"),
            },
        }


# ==============================================================================
# 構築(env secret → Gateway・llm_attempts記録つきadapter)
# ==============================================================================


def default_parser_assets() -> tuple[str, dict]:
    """Parserのプロンプトと出力schema(本体と同一資産・cf-syncで同梱される)。"""
    from latch.intents.prompt import PARSER_SYSTEM_PROMPT
    from latch.intents.schema import ParserOutput

    return PARSER_SYSTEM_PROMPT, ParserOutput.model_json_schema()


def real_env_config(env) -> dict:
    """env(wrangler vars+secret)からreal構成の引数を集める。"""
    return {
        "anthropic_key": _env_var(env, "LATCH_ANTHROPIC_API_KEY"),
        "gemini_key": _env_var(env, "LATCH_GEMINI_API_KEY"),
        "typesafe_key": _env_var(env, "LATCH_TYPESAFE_API_KEY"),
        "anthropic_base_url": _env_var(env, "LATCH_ANTHROPIC_BASE_URL") or None,
        "typesafe_base_url": _env_var(env, "LATCH_TYPESAFE_BASE_URL") or None,
    }


def build_real_gateway(
    clock,
    *,
    anthropic_key: str,
    gemini_key: str,
    typesafe_key: str,
    parser_system_prompt: str | None = None,
    parser_output_schema: dict | None = None,
    anthropic_base_url: str | None = None,
    typesafe_base_url: str | None = None,
):
    """real構成のLLMGateway(timeout・フォールバック・breaker・送信記録は本体実装)。

    鍵・プロンプト・スキーマの欠落はfail-fast(静かにスタブへ落ちない —
    build_worker_gatewayと同じ規律)。base_urlは試験・回避経路用の上書きのみ
    (未指定なら公式API)。
    """
    from latch.llm.breaker import CircuitBreaker
    from latch.llm.gateway import LLMGateway

    if not anthropic_key:
        raise ValueError("real LLM requires LATCH_ANTHROPIC_API_KEY")
    if not gemini_key:
        raise ValueError("real LLM requires LATCH_GEMINI_API_KEY")
    if not typesafe_key:
        raise ValueError("real LLM requires LATCH_TYPESAFE_API_KEY")
    if parser_system_prompt is None or parser_output_schema is None:
        raise ValueError("real LLM requires parser_system_prompt and parser_output_schema")
    parser_kwargs = (
        {"base_url": anthropic_base_url} if anthropic_base_url else {}
    )
    jev_kwargs = {"base_url": typesafe_base_url} if typesafe_base_url else {}
    fallback_kwargs = (
        {"base_url": anthropic_base_url} if anthropic_base_url else {}
    )
    return LLMGateway(
        clock=clock,
        parser=FetchAnthropicParser(
            api_key=anthropic_key,
            system_prompt=parser_system_prompt,
            output_schema=parser_output_schema,
            **parser_kwargs,  # type: ignore[arg-type]
        ),
        embedding=FetchGeminiEmbedding(api_key=gemini_key),
        jev=FetchTypeSafeJev(api_key=typesafe_key, **jev_kwargs),  # type: ignore[arg-type]
        jev_fallback=FetchAnthropicJevFallback(
            api_key=anthropic_key, **fallback_kwargs  # type: ignore[arg-type]
        ),
        breaker=CircuitBreaker(clock=clock),
    )


class RecordingParser:
    """parse呼び出しにllm_attempts記録を添えるwrapper(real時のみ・state.pyで使用)。

    Gateway経由の送信記録(構造化ログ)に加え、D1のllm_attemptsへ
    system='parser'の試行を残す(jevaluationと同じprepare→finish規律)。
    token数はGateway契約上ここからは見えないためNULL(不明として記録)。
    """

    def __init__(self, gateway, recorder, clock) -> None:
        self._gateway = gateway
        self._recorder = recorder
        self._clock = clock
        self.name = "anthropic"

    async def parse_intent(
        self, *, text: str, current_date: date, user_id: str | None = None
    ) -> dict:
        attempt = await self._recorder.prepare(
            system="parser",
            requested_provider="anthropic",
            requested_model=ANTHROPIC_PARSER_MODEL,
            price_version=PRICE_VERSION_REAL,
            now=self._clock.now(),
        )
        try:
            result = await self._gateway.parse_intent(
                text=text, current_date=current_date, user_id=user_id
            )
        except Exception as exc:  # noqa: BLE001 — 記録して素通り
            await self._recorder.finish(
                attempt,
                status="failed",
                now=self._clock.now(),
                error_kind=type(exc).__name__,
            )
            raise
        await self._recorder.finish(
            attempt,
            status="succeeded",
            now=self._clock.now(),
            actual_provider="anthropic",
            actual_model=ANTHROPIC_PARSER_MODEL,
        )
        return result


class RealPipelineLLM:
    """pipeline用real adapter(embeddingのllm_attempts記録+judgeはGatewayへ)。

    embed: prepare→Gateway.embed_intent→finish(stub経路にはなかった
    embedding試行記録をreal時のみ追加。失敗も記録して再送は上位の
    at-least-onceに任せる)。judge: judge_pair(フォールバック・breaker込み)
    のJevJudgmentをそのまま返す(llm_attempts記録はEvaluationJobRunnerが担う)。
    """

    mode = "real"

    def __init__(self, gateway, recorder, clock) -> None:
        self._gateway = gateway
        self._recorder = recorder
        self._clock = clock

    async def embed(self, text: str, *, intent_id: str) -> list[float]:
        attempt = await self._recorder.prepare(
            system="embedding",
            requested_provider="google",
            requested_model=GEMINI_EMBEDDING_MODEL,
            price_version=PRICE_VERSION_REAL,
            now=self._clock.now(),
        )
        try:
            vector = await self._gateway.embed_intent(text=text, intent_id=intent_id)
        except Exception as exc:  # noqa: BLE001 — 記録して素通り(上位で再試行)
            await self._recorder.finish(
                attempt,
                status="failed",
                now=self._clock.now(),
                error_kind=type(exc).__name__,
            )
            raise
        await self._recorder.finish(
            attempt,
            status="succeeded",
            now=self._clock.now(),
            actual_provider="google",
            actual_model=GEMINI_EMBEDDING_MODEL,
        )
        return vector

    async def judge(
        self, intent_a: str, intent_b: str, *, intent_ids: list[str]
    ) -> JevJudgment:
        return await self._gateway.judge_pair(
            intent_a=intent_a, intent_b=intent_b, intent_ids=intent_ids
        )


def build_real_pipeline_llm(env, clock, db) -> RealPipelineLLM:
    """pipeline.build_pipelineのreal経路(env secretから一括構築)。"""
    from latch.adapters.d1.attempts import D1AttemptRecorder

    prompt, schema = default_parser_assets()
    gateway = build_real_gateway(
        clock, parser_system_prompt=prompt, parser_output_schema=schema,
        **real_env_config(env),
    )
    return RealPipelineLLM(gateway, D1AttemptRecorder(db), clock)


__all__ = [
    "ANTHROPIC_BASE_URL",
    "FetchAnthropicJevFallback",
    "FetchAnthropicParser",
    "FetchGeminiEmbedding",
    "FetchTypeSafeJev",
    "PRICE_VERSION_REAL",
    "RealPipelineLLM",
    "RecordingParser",
    "build_real_gateway",
    "build_real_pipeline_llm",
    "default_parser_assets",
    "real_env_config",
    "set_transport",
]
