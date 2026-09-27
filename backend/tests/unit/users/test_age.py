"""満年齢純粋関数 age_years: 暦日境界の全パターン(design §2.3・§4.1-1)。

判定は常に `age_years(birth, clock.jst_date()) < 18` の組合せで使う
(18歳の誕生日当日=18=登録可・前日=17=UNDER_AGE)。
"""

from datetime import date

from latch.users.errors import (
    DependencyUnavailableError,
    UnderAgeError,
    UserExistsError,
    UserNotFoundError,
)
from latch.users.service import age_years


def test_error_statuses_and_codes():
    # 05 第5節 エラー形式表の対応。ハンドラはこれを信じてstatus/codeを出す
    assert (UnderAgeError("x").http_status, UnderAgeError("x").code) == (
        422,
        "UNDER_AGE",
    )
    assert (UserExistsError("x").http_status, UserExistsError("x").code) == (
        409,
        "USER_EXISTS",
    )
    assert (UserNotFoundError("x").http_status, UserNotFoundError("x").code) == (
        404,
        "NOT_FOUND",
    )
    assert (
        DependencyUnavailableError("x").http_status,
        DependencyUnavailableError("x").code,
    ) == (503, "DEPENDENCY_UNAVAILABLE")


def test_18th_birthday_today_is_18():
    # 18歳の誕生日当日=18(登録可 — design §2.3)
    assert age_years(date(2008, 9, 28), date(2026, 9, 28)) == 18


def test_day_before_18th_birthday_is_17():
    assert age_years(date(2008, 9, 28), date(2026, 9, 27)) == 17


def test_feb29_birthday_in_common_year_adds_on_mar1():
    # 2月29日生まれ: うるう年でない年は3月1日に年齢が加算(タプル比較。
    # 法律の前日主義(2/28)と1日ずれるが自己申告値のため許容 — design §2.3)
    assert age_years(date(2008, 2, 29), date(2026, 2, 28)) == 17
    assert age_years(date(2008, 2, 29), date(2026, 3, 1)) == 18


def test_feb29_birthday_on_leap_year_today():
    assert age_years(date(2008, 2, 29), date(2028, 2, 29)) == 20


def test_future_birth_date_is_negative():
    # 未来日付=負の年齢=18歳未満としてUNDER_AGEで拒否(特別な検証を足さない)
    assert age_years(date(2030, 1, 1), date(2026, 9, 27)) == -4


def test_dec31_birthday_on_jan1_counts_previous_birthday():
    # 年またぎ: 12/31生まれの1/1は直前の誕生日まで数える
    assert age_years(date(2008, 12, 31), date(2026, 1, 1)) == 17
    assert age_years(date(2008, 12, 31), date(2026, 12, 31)) == 18


def test_adult_birth_date_various():
    assert age_years(date(1990, 4, 1), date(2026, 9, 27)) == 36
    assert age_years(date(2008, 9, 27), date(2026, 9, 27)) == 18
