"""認証例外階層(design §3.1)。

各例外は http_status と code(05 第5節のerror code)を固定で持つ。
routes のハンドラはこれを共通envelopeへ変換する。例外メッセージには
トークン文字列・claim内容・subjectを含めない(08 第2.4節)。
"""

from __future__ import annotations


class AuthError(Exception):
    """認証系エラーの基底。http_status/code を持つ(ハンドラが消費する)。"""

    http_status: int
    code: str


class InvalidIdpTokenError(AuthError):
    """JWKS署名検証失敗・iss/aud不一致・期限切れ等(05 第5節)。"""

    http_status = 401
    code = "INVALID_IDP_TOKEN"


class UnauthenticatedError(AuthError):
    """API発行JWTの無効・期限切れ・失効リスト掲載(05 第5節)。"""

    http_status = 401
    code = "UNAUTHENTICATED"


class InvalidRefreshTokenError(AuthError):
    """リフレッシュトークン不在・期限切れ・回転済み再提示(05 第5節)。"""

    http_status = 401
    code = "INVALID_REFRESH_TOKEN"


class DependencyUnavailableError(AuthError):
    """DB・Redis・JWKS取得等の依存障害(05 第5節。全API)。"""

    http_status = 503
    code = "DEPENDENCY_UNAVAILABLE"
