"""LLM Gateway例外階層(design §3.1)。

08 第2.4節「例外・エラーはIDのみ」— 例外メッセージにIntent本文を混ぜない。
"""

from __future__ import annotations


class LLMError(Exception):
    """LLM Gateway経由の呼び出し失敗の基底。"""


class LLMTimeoutError(LLMError):
    """系統別timeout超過(07 第1節)。再試行なし — 呼び出し側は縮退/フォールバックへ。"""


class LLMProviderError(LLMError):
    """プロバイダ側の失敗(10 第4.5節の100%エラー注入が再現する状態)。"""


class JevOutputInvalidError(LLMError):
    """出力検証失敗(07 §4)。再試行も切替もしない(実装不整合として扱う)。"""

    def __init__(self, msg: str, *, provider: str | None = None) -> None:
        super().__init__(msg)
        self.provider = provider  # 検証対象の経路("typesafe_jev" | "fallback_llm")


class LLMRateLimitError(LLMError):
    """429(07 §4切替条件)。第一候補のSDK backoff retryは無効化済みで切替へ。"""


class LLMOverloadedError(LLMError):
    """529(07 §4切替条件)。第一候補のSDK backoff retryは無効化済みで切替へ。"""


class LLMConnectionError(LLMError):
    """接続障害(07 §4切替条件)。timeoutと同じく再試行なしで切替へ。"""
