"""LLM Gateway(M0 ws-2)。3系統集約・送信記録・プロバイダ抽象・スタブ。

M1/M2の呼び出し側はこのパッケージ越しにGatewayを利用する(design §3.1)。
"""

from latch.llm.errors import (
    JevOutputInvalidError,
    LLMConnectionError,
    LLMError,
    LLMOverloadedError,
    LLMProviderError,
    LLMRateLimitError,
    LLMTimeoutError,
)
from latch.llm.gateway import (
    TIMEOUT_EMBEDDING_S,
    TIMEOUT_JEV_S,
    TIMEOUT_PARSER_S,
    LLMGateway,
    Timeouts,
    build_llm_gateway,
    build_worker_gateway,
)
from latch.llm.gemini import GeminiEmbeddingProvider
from latch.llm.jev import JEV_MODEL, JevJudgment
from latch.llm.providers import (
    EMBEDDING_DIMENSIONS,
    EmbeddingProvider,
    JevProvider,
    ParserProvider,
)
from latch.llm.records import SendRecord, send_log
from latch.llm.stub import StubLLM
from latch.llm.anthropic_jev import AnthropicJevFallbackProvider
from latch.llm.typesafe import TypeSafeJevProvider

__all__ = [
    "EMBEDDING_DIMENSIONS",
    "AnthropicJevFallbackProvider",
    "EmbeddingProvider",
    "GeminiEmbeddingProvider",
    "JEV_MODEL",
    "JevJudgment",
    "JevOutputInvalidError",
    "JevProvider",
    "LLMConnectionError",
    "LLMError",
    "LLMGateway",
    "LLMOverloadedError",
    "LLMProviderError",
    "LLMRateLimitError",
    "LLMTimeoutError",
    "ParserProvider",
    "SendRecord",
    "StubLLM",
    "TIMEOUT_EMBEDDING_S",
    "TIMEOUT_JEV_S",
    "TIMEOUT_PARSER_S",
    "Timeouts",
    "TypeSafeJevProvider",
    "build_llm_gateway",
    "build_worker_gateway",
    "send_log",
]
