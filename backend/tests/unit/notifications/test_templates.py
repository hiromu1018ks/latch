"""プッシュ文面テンプレートのunit試験(M3 ws-3 design §2.4・§4.1-1)。

FR-21(03 §4・08 §2.6)の対: 文言定数の全文ピン(恒久固定)と、
build_pushがtypeのみから固定文言へ写像すること(構造担保)を検証する。
"""

import pytest

from latch.notifications.templates import (
    PUSH_BODY_LATCH,
    PUSH_BODY_NOTICE,
    PUSH_TITLE,
    build_push,
)
from latch.notifications.types import (
    NOTIFICATION_ATTENDANCE_REQUEST,
    NOTIFICATION_NEARBY,
    NOTIFICATION_PROPOSAL,
)


def test_push_constants_full_text():
    """3定数の全文ピン(design §2.4初期値・03 §4様式)。"""
    assert PUSH_TITLE == "LATCH"
    assert PUSH_BODY_LATCH == "LATCH候補があります。\n詳細はアプリでご確認ください。"
    assert (
        PUSH_BODY_NOTICE
        == "LATCHからのお知らせがあります。\n詳細はアプリでご確認ください。"
    )


def test_build_push_maps_all_types():
    """全typeが(title, body)を返す。proposal/nearbyは同一文言(§2.4承認②)。"""
    latch_text = (PUSH_TITLE, PUSH_BODY_LATCH)
    assert build_push(NOTIFICATION_PROPOSAL) == latch_text
    assert build_push(NOTIFICATION_NEARBY) == latch_text
    assert build_push(NOTIFICATION_ATTENDANCE_REQUEST) == (
        PUSH_TITLE,
        PUSH_BODY_NOTICE,
    )


def test_build_push_unknown_type_raises():
    """未知typeはValueError(fail-fast・握らない — design §2.7)。"""
    with pytest.raises(ValueError, match="notification_type"):
        build_push("unknown_type")


def test_build_push_output_has_no_condition_summary():
    """戻り値に条件サマリ語彙が現れない(FR-21・引用#1)。

    build_pushの戻り値は定数のみを通り得る。proposal語彙(time_summary・
    area_name・category・headcount・budget・match_level等)が将来の変更で
    混入した場合、この試験が検知する。
    """
    forbidden = (
        "time_summary",
        "area_name",
        "category",
        "headcount",
        "budget",
        "match_level",
        "summary_only",
        "hidden_until_match",
        "drinking",
        "meal",
        "焼肉",
        "天文館",
    )
    for ntype in (
        NOTIFICATION_PROPOSAL,
        NOTIFICATION_NEARBY,
        NOTIFICATION_ATTENDANCE_REQUEST,
    ):
        title, body = build_push(ntype)
        for word in forbidden:
            assert word not in title, (ntype, word)
            assert word not in body, (ntype, word)
