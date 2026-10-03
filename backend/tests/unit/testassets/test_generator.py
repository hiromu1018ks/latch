"""決定的シード生成器のunit試験(M4 ws-2・design §4-1・4-2)。

決定性(バイト同一)・分布比率の実現・意図的配置の成立・副次分布を検証する。
300件フル生成は全ペアL1判定(44,850組)を含むが純Pythonで1〜2秒程度。
"""

from latch.testassets.vocab import (
    CATEGORY_SECONDARY,
    LOCATIONS,
    RAW_TEMPLATES,
    SOFT_CONSTRAINTS,
    TIME_PREFIX,
    time_bucket_of,
)

# --- 0. 語彙プール(M4 ws-2 Task 1) ---


def test_vocab_pools_are_valid():
    """語彙プールの基本性質(7地名・bbox内・カテゴリ網羅・テンプレート整合)。"""
    assert (
        len(LOCATIONS) == 7
    )  # 9→7はsupervisor裁定2026-10-03(ci-db実査・正転ヒット済みのみ)
    for lat, lon in LOCATIONS.values():  # settings.py geo_osm_bbox
        assert 31.5825 <= lat <= 31.6095
        assert 130.5420 <= lon <= 130.5740
    assert set(CATEGORY_SECONDARY) == {"drinking", "meal", "activity"}
    assert set(SOFT_CONSTRAINTS) == {"drinking", "meal", "activity"}
    assert set(RAW_TEMPLATES) == {"drinking", "meal", "activity"}
    for tpls in RAW_TEMPLATES.values():
        for t in tpls:
            assert t.format(time_prefix="x", place="y", secondary="z", soft="w")
    assert set(TIME_PREFIX) == {"day", "evening", "night", "other"}
    assert time_bucket_of(12) == "day"  # 昼=12:00-13:59(design §2.4)
    assert time_bucket_of(18) == "evening"  # 夕=18:00-19:59
    assert time_bucket_of(20) == "night"  # 夜=20:00-22:59
    assert time_bucket_of(13) == "day"
    assert time_bucket_of(19) == "evening"
    assert time_bucket_of(22) == "night"
    assert time_bucket_of(11) == "other"  # その他=上記以外
    assert time_bucket_of(14) == "other"
    assert time_bucket_of(23) == "other"
