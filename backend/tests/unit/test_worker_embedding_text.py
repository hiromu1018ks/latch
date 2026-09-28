"""正規化テキスト導出の純関数試験(design §2.4・§4.1)。

07 §3の形式仕様と例示(月曜20:00-23:00 → 平日夜20-23時)を全文ピンで
拘束する。入力dataclassがraw_textを保持しないことの構造ピン(確定値#10)。
帯区分表はdocs規定がなく実装定義のため、ここで全文を固定する。
"""

import dataclasses
from datetime import datetime

from latch.core.clock import JST
from latch.worker.embedding_text import EmbeddingTextInput, build_embedding_text

# 2026-09-28は月曜(平日)。JST 20:00-23:00 = 07 §3の例と同一時刻
MON_20_23_START = datetime(2026, 9, 28, 20, 0, tzinfo=JST)
MON_20_23_END = datetime(2026, 9, 28, 23, 0, tzinfo=JST)


def _inp(**overrides) -> EmbeddingTextInput:
    base = dict(
        category_primary="drinking",
        structured_data={
            "location_name": "天文館",
            "soft_constraints": [
                {"text": "静かなお店で", "downgraded_from_ng": False},
                {"text": "予算は抑えめ", "downgraded_from_ng": True},
            ],
        },
        participants_min=2,
        participants_max=4,
        time_start=MON_20_23_START,
        time_end=MON_20_23_END,
    )
    base.update(overrides)
    return EmbeddingTextInput(**base)


def test_reference_example_from_07_section3():
    """07 §3の例どおり: 月曜20:00-23:00 → 『平日夜20-23時』を含む全文。"""
    text = build_embedding_text(_inp())
    assert text == (
        "drinking / 平日夜20-23時 / 天文館 / 2-4人 / 静かなお店で・予算は抑えめ"
    )


def test_soft_constraints_empty_omits_trailing_separator():
    """soft_constraints空 → 末尾の「 / 」が残らない(空要素の省略)。"""
    inp = _inp(structured_data={"location_name": "天文館", "soft_constraints": []})
    assert build_embedding_text(inp) == "drinking / 平日夜20-23時 / 天文館 / 2-4人"


def test_band_table_boundaries():
    """帯区分表: 5≤h<11朝/11≤h<16昼/16≤h<19夕方/19≤h<23夜/その他(23・0〜4時)深夜。"""
    cases = {  # 開始時刻(JST) → 帯(全て月曜=平日)
        4: "深夜",
        5: "朝",
        10: "朝",
        11: "昼",
        15: "昼",
        16: "夕方",
        18: "夕方",
        19: "夜",
        22: "夜",
        23: "深夜",
        0: "深夜",
    }
    for hour, band in cases.items():
        inp = _inp(
            time_start=datetime(2026, 9, 28, hour, 0, tzinfo=JST),
            time_end=datetime(2026, 9, 28, hour + 1, 0, tzinfo=JST)
            if hour < 23
            else None,
        )
        text = build_embedding_text(inp)
        assert f"平日{band}" in text, (hour, text)


def test_weekend_day_of_week():
    """土曜19:00-22:00 → 週末夜(design §2.4の曜日規則)。"""
    inp = _inp(
        time_start=datetime(2026, 9, 26, 19, 0, tzinfo=JST),  # 2026-09-26は土曜
        time_end=datetime(2026, 9, 26, 22, 0, tzinfo=JST),
    )
    assert "週末夜19-22時" in build_embedding_text(inp)


def test_overnight_end_next_day():
    """日跨ぎ: 23:00〜翌2:00 → 深夜23-2時(endのJST時刻を使う)。"""
    inp = _inp(
        time_start=datetime(2026, 9, 28, 23, 0, tzinfo=JST),
        time_end=datetime(2026, 9, 29, 2, 0, tzinfo=JST),
    )
    assert "深夜23-2時" in build_embedding_text(inp)


def test_time_end_null_uses_以降():
    """time_end NULL → 『{start.hour}時以降』。"""
    inp = _inp(time_end=None)
    assert "平日夜20時以降" in build_embedding_text(inp)


def test_time_start_null_omits_time_element():
    """time_start NULL → 時間帯要素全体を省略(05 §2想定外値の防御)。"""
    inp = _inp(time_start=None, time_end=None)
    assert build_embedding_text(inp) == (
        "drinking / 天文館 / 2-4人 / 静かなお店で・予算は抑えめ"
    )


def test_headcount_equal_min_max():
    """min == max → 『{N}人』。"""
    inp = _inp(participants_min=3, participants_max=3)
    assert " / 3人 / " in build_embedding_text(inp)


def test_location_missing_omits_element():
    """location_name欠損 → 要素を省略(区切りが連続しない)。"""
    inp = _inp(structured_data={"location_name": None, "soft_constraints": []})
    assert build_embedding_text(inp) == "drinking / 平日夜20-23時 / 2-4人"


def test_input_dataclass_has_no_raw_text_field():
    """入力dataclassはraw_textを保持しない(確定値#10の構造ピン)。"""
    field_names = {f.name for f in dataclasses.fields(EmbeddingTextInput)}
    assert "raw_text" not in field_names
    assert field_names == {
        "category_primary",
        "structured_data",
        "participants_min",
        "participants_max",
        "time_start",
        "time_end",
    }
