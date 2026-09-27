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
