"""レート制限例外階層(design §2.6)。

各例外は http_status と code(05 第5節のerror code)を固定で持つ。main.py の
ハンドラが共通envelopeへ変換する。例外メッセージにはユーザー由来の内容
(subject・text)を含めない(08 §2.4)— 固定文言のみ。
"""

from __future__ import annotations


class RateLimitError(Exception):
    """レート制限系エラーの基底。http_status/code を持つ(ハンドラが消費する)。"""

    http_status: int
    code: str


class RateLimitedError(RateLimitError):
    """上限超過(05 第5節。適用範囲=全API)。"""

    http_status = 429
    code = "RATE_LIMITED"


class RateLimitDependencyError(RateLimitError):
    """Redis接続障害(fail-closed — 05 第5節 DEPENDENCY_UNAVAILABLE・design §2.6)。"""

    http_status = 503
    code = "DEPENDENCY_UNAVAILABLE"
