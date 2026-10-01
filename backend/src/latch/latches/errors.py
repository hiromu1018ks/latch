"""latchesドメイン例外階層(M3 ws-1 design §2.9・§3.1)。

各例外は http_status と code(05 §5エラー形式表)を固定で持つ。main.py の
ハンドラが共通envelopeへ変換する。例外メッセージにProposal本文・回答内容
を混ぜない(08 §2.4)— 呼び出し側は固定文言のみを渡す(§9-13)。
"""

from __future__ import annotations


class LatchesError(Exception):
    """latchesドメインエラーの基底。http_status/code を持つ(ハンドラが消費する)。"""

    http_status: int
    code: str


class LatchNotFoundError(LatchesError):
    """対象LATCHが存在しない。未登録JWT(User行なし)も同じ404(引用#17)。"""

    http_status = 404
    code = "NOT_FOUND"


class ForbiddenError(LatchesError):
    """参加者以外の操作(05 §5)。存在秘匿の404ではなく403(引用#17)。"""

    http_status = 403
    code = "FORBIDDEN"


class LatchValidationError(LatchesError):
    """リクエスト検証422(cursor形式不正等・intentsのIntentValidationError同型)。"""

    http_status = 422
    code = "VALIDATION_ERROR"


class LatchExpiredError(LatchesError):
    """回答期限切れ(response_deadline・expires_at経過・引用#1)。"""

    http_status = 409
    code = "LATCH_EXPIRED"


class AlreadyAnsweredError(LatchesError):
    """同一ユーザーの二重回答(引用#1)。"""

    http_status = 409
    code = "ALREADY_ANSWERED"


class LatchClosedError(LatchesError):
    """終端済み・未提示candidate・参加Intent変化によるクローズ(引用#1・01 §8)。"""

    http_status = 409
    code = "LATCH_CLOSED"


class DependencyUnavailableError(LatchesError):
    """DB・Redis等の依存障害(05 §5「全API」)。"""

    http_status = 503
    code = "DEPENDENCY_UNAVAILABLE"


class ChatReadonlyError(LatchesError):
    """チャット読取専用状態への送信(05 §2・08 D-23・design §2.1案A)。

    matched以外の全状態(completed/cancelled後の送信关闭を含む)と
    blocks適用中の両方に使う単一コード(03 §6・08 §2.5の統一・承認事項②)。
    """

    http_status = 409
    code = "CHAT_READONLY"


class AttendanceAlreadySubmittedError(LatchesError):
    """実施自己申告の二重回答(初回のみ受理・訂正不可・05 §5)。

    LATCH単位先着1名のため2人目以降の回答もこれに含む(承認事項①)。
    """

    http_status = 409
    code = "ATTENDANCE_ALREADY_SUBMITTED"


class AttendanceWindowClosedError(LatchesError):
    """申告窓閉鎖(completed遷移から3日経過・D-09・design §2.3手順5)。"""

    http_status = 409
    code = "ATTENDANCE_WINDOW_CLOSED"
