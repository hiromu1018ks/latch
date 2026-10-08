"""JST暦日helper・時刻定数(T2: layer4/latch_calcから分離)。

Clock由来の暦日導出を一元化する(C2規律)。SQLAlchemy依存のlayer4から
domainへ移し、純計算モジュール(latch_calc等)がDB依存へ到達しない
ようにする。旧import経路(layer4・latch_calc)は再exportで存続させる。
"""

from __future__ import annotations

from datetime import UTC, date, datetime, time, timedelta, timezone

# JST(UTC+9・固定オフセット)。定義の単一源はここ(core.clock は再export)。
JST = timezone(timedelta(hours=+9))


def jst_day_start(d: date) -> datetime:
    """JST暦日の0時(再選択境界。C2: Clock.jst_date()から導出)。"""
    return datetime.combine(d, time(0, 0), tzinfo=JST)


def jst_month_start(d: date) -> datetime:
    """JST暦月の1日0時(月次上限の境界。layer4から移設・意味不変)。"""
    return datetime.combine(d.replace(day=1), time(0, 0), tzinfo=JST)


def to_utc(dt: datetime) -> datetime:
    """tz-aware時刻をUTCへ正規化(naiveはJSTと解釈しない・例外)。"""
    if dt.tzinfo is None:
        raise ValueError("naive datetime is not allowed in domain time helpers")
    return dt.astimezone(UTC)


def micros(dt: datetime) -> int:
    """UTC整数マイクロ秒表現(D1保存形式 — 計画§3.1)。"""
    return int(to_utc(dt).timestamp() * 1_000_000)


def from_micros(value: int) -> datetime:
    """UTC整数マイクロ秒 → tz-aware datetime(D1保存形式の復元)。"""
    return datetime.fromtimestamp(value / 1_000_000, tz=UTC)


def age_years(birth_date, today) -> int:
    """満年齢(users.service.age_years と同一・T2分離)。

    (月, 日)タプル比較 — 誕生日当日に加算。2月29日生まれは、その暦日に
    2月29日を持たない年は3月1日に加算(1日の差は許容して比較を単純に保つ)。
    """
    return (
        today.year
        - birth_date.year
        - ((today.month, today.day) < (birth_date.month, birth_date.day))
    )
