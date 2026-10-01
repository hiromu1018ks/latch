"""safetyドメイン例外階層(M3 ws-5 design §2.1・§3)。

ブロック・通報は専用error codeを持たない(引用#15 — 05 §5の共通codeで
構成する)。各例外はhttp_statusとcodeを固定で持ち、main.pyのハンドラが
共通envelopeへ変換する。例外メッセージは固定文言のみ(08 §2.4)。
"""

from __future__ import annotations


class SafetyError(Exception):
    """safetyドメインエラーの基底。http_status/codeを持つ(ハンドラが消費)。"""

    http_status: int
    code: str


class SafetyNotFoundError(SafetyError):
    """対象不在(未登録JWT・相手ユーザー不在・blocks行なし・latch不在)。"""

    http_status = 404
    code = "NOT_FOUND"


class SafetyValidationError(SafetyError):
    """リクエスト検証422(自分自身への操作・cursor形式不正・latch非参加)。"""

    http_status = 422
    code = "VALIDATION_ERROR"


class SafetyDependencyUnavailableError(SafetyError):
    """DB・Redis等の依存障害(05 §5「全API」)。"""

    http_status = 503
    code = "DEPENDENCY_UNAVAILABLE"
