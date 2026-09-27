"""usersユースケース(M1 ws-1。design §2.1〜§2.5)。

永続化は text() 生SQLの関数(INSERT 1本+SELECT 1本)をCallableで注入する
(auth の user_lookup 注入と同型。ORMモデル・リポジトリ層の導入判断は
intents CRUD(ws-3 M1)に委ねる — design §2.1)。時刻列はClock由来の明示値
(DB時刻関数のDEFAULTは使わない)。UserService・make_user_serviceは後続タスクで
このファイルへ追加する。
"""

from __future__ import annotations

from datetime import date


def age_years(birth_date: date, today: date) -> int:
    """満年齢(design §2.3)。(月, 日)タプル比較 — 誕生日当日に加算。

    2月29日生まれは、その暦日に2月29日を持たない年は3月1日に加算される
    (自己申告値のため1日の差は許容して比較を単純に保つ)。
    """
    return (
        today.year
        - birth_date.year
        - ((today.month, today.day) < (birth_date.month, birth_date.day))
    )
