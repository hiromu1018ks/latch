"""users例外階層(M1 ws-1 design §3.1)。

auth/errors.py と同型 — 各例外は http_status と code(05 第5節のerror code)を
固定で持つ。auth側(AuthError)との共通基底は作らない(auth不変 — design §2.6。
main.py に users_error_handler を1個追加するだけ)。
例外メッセージにsubject・claim内容・display_nameを含めない(08 第2.4節)。
"""

from __future__ import annotations


class UsersError(Exception):
    """users系エラーの基底。http_status/code を持つ(ハンドラが消費する)。"""

    http_status: int
    code: str


class UnderAgeError(UsersError):
    """18歳未満の初回登録拒否(05 第5節・08 第4節 D-10)。"""

    http_status = 422
    code = "UNDER_AGE"


class ConsentRequiredError(UsersError):
    """同意なしでの利用(422 CONSENT_REQUIRED・08 D-14)。

    本単位では送出経路なし(intents側が送出)。05 §5のusers系エラー列挙に
    対向する配置(UnderAgeErrorの二重配置先例と同型 — 計画§4-4)。
    """

    http_status = 422
    code = "CONSENT_REQUIRED"


class UserExistsError(UsersError):
    """登録済みauth_subjectでの初回登録(05 第5節)。"""

    http_status = 409
    code = "USER_EXISTS"


class UserNotFoundError(UsersError):
    """User行不在(GET /v1/users/me — design §2.4の404解釈)。"""

    http_status = 404
    code = "NOT_FOUND"


class DependencyUnavailableError(UsersError):
    """DB等の依存障害(05 第5節「全API」)。"""

    http_status = 503
    code = "DEPENDENCY_UNAVAILABLE"


class SurveyAlreadyAnsweredError(UsersError):
    """アンケート回答の二重回答(ラウンド単位・訂正不可。05 v0.7)。"""

    http_status = 409
    code = "SURVEY_ALREADY_ANSWERED"


class SurveyNotDueError(UsersError):
    """配信対象外ラウンドへの回答(05 v0.7。onboarding=active化Intentなし/
    week4=初回回答から28日未経過または初回未回答)。"""

    http_status = 409
    code = "SURVEY_NOT_DUE"
