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


class IntentValidationError(IntentsError):
    """保存APIの入力検証422(必須3フィールド欠落・時刻範囲外・cursor不正)。

    parse経路のUnstructurableError(LLM出力不正)と違い、保存経路のリクエスト
    検証用(design §2.3)。
    """

    http_status = 422
    code = "VALIDATION_ERROR"


class UnderAgeError(IntentsError):
    """20歳未満の飲酒Intent作成・更新(08 D-10)。"""

    http_status = 422
    code = "UNDER_AGE"


class GeocodingFailedError(IntentsError):
    """location.nameに該当する地物が存在しない(05 §5・04 §3)。"""

    http_status = 422
    code = "GEOCODING_FAILED"


class IntentNotFoundError(IntentsError):
    """対象Intentが存在しない。未登録JWT(User行なし)も同じ404(design §2.6)。"""

    http_status = 404
    code = "NOT_FOUND"


class ForbiddenError(IntentsError):
    """所有者以外の操作(05 §5)。存在秘匿の404ではなく403(design §6-5f)。"""

    http_status = 403
    code = "FORBIDDEN"


class InvalidTransitionError(IntentsError):
    """遷移表にない操作(draftのpause・matched行のDELETE・逆遷移等・design §6-5e)。"""

    http_status = 422
    code = "VALIDATION_ERROR"
