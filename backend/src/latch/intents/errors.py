"""intentsドメイン例外階層(design §2.7・§3.1)。

各例外は http_status と code(05 §5エラー形式表)を固定で持つ。main.py の
ハンドラが共通envelopeへ変換する。例外メッセージにIntent本文(text・condition)
を混ぜない(08 §2.4)— 呼び出し側は固定文言のみを渡す。
"""

from __future__ import annotations


class IntentsError(Exception):
    """intentsドメインエラーの基底。http_status/code を持つ(ハンドラが消費する)。"""

    http_status: int
    code: str


class LLMUnavailableError(IntentsError):
    """Parser系LLM障害(timeout・API障害・レート制限。07 §2・05 §5)。

    クライアントは入力テキストを保持した再試行ボタンへ分岐する(D-17)。
    """

    http_status = 503
    code = "LLM_UNAVAILABLE"


class UnstructurableError(IntentsError):
    """構造化不能(必須3フィールド抽出不能・スキーマ不適合。07 §2・design §2.4)。

    クライアントは構造化フォームフォールバックへ分岐する(D-17・FR-43)。
    """

    http_status = 422
    code = "VALIDATION_ERROR"


class DependencyUnavailableError(IntentsError):
    """DB・Redis等の依存障害(05 §5「全API」。user_lookup失敗・design §2.3)。"""

    http_status = 503
    code = "DEPENDENCY_UNAVAILABLE"
