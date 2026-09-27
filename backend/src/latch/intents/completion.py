"""D-19アプリ層補完の単一規則(07 §2解釈規則・03 §3)。

Parserは抽出のみを行い、デフォルト補完はこのモジュールが単一実装として
持つ(二重実装による不整合を防ぐ — 07 §2)。消費者は保存経路(ws-3)と
UI計算(ws-5)。時刻参照は行わない(引数で受け取る — C2)。
"""

from __future__ import annotations

from datetime import date, datetime, time, timedelta

from latch.core.clock import JST

# 07 §2解釈規則(03 D-19)の確定値
DEFAULT_RADIUS_M = 1000  # location.radius_m の既定半径(メートル)
DEFAULT_PARTICIPANTS = (2, 2)  # participants の既定(min, max)

DEFAULT_DURATION = timedelta(hours=3)  # time.end と期限既定の基準幅


def default_time_end(time_start: datetime) -> datetime:
    """time.end null → time.start + 3時間(07 §2解釈規則)。"""
    return time_start + DEFAULT_DURATION


def _at_jst(day: date, hour: int, minute: int) -> datetime:
    return datetime.combine(day, time(hour, minute), tzinfo=JST)


def expires_at_candidates(now: datetime) -> list[datetime]:
    """有効期限4選択肢(03 §3 FR-13)。UIの選択肢順で返す。

    今夜23:30=当日JST 23:30 / 明日12:00・明日23:30=翌日JST / 3日後まで=now+72h。
    now は tz-aware(Clock.now() と同じ UTC 契約)。過ぎた候補の除外は
    nearest_expires_at 側で行う(一覧表示にも候補全値が要るため)。
    """
    jst_today = now.astimezone(JST).date()
    tomorrow = jst_today + timedelta(days=1)
    return [
        _at_jst(jst_today, 23, 30),
        _at_jst(tomorrow, 12, 0),
        _at_jst(tomorrow, 23, 30),
        now + timedelta(hours=72),
    ]


def nearest_expires_at(time_start: datetime, now: datetime) -> datetime:
    """既定の期限=time.start+3時間に最も近い選択可能候補(03 §3)。

    選択可能=候補が now より未来(過ぎた選択肢は選択不可)。同点は最早。
    time_start が過去でも成立する(target との距離順で最早の未来候補が選ばれる)。
    """
    target = default_time_end(time_start)
    selectable = [c for c in expires_at_candidates(now) if c > now]
    return min(selectable, key=lambda c: (abs(c - target), c))
