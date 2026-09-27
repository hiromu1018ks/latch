"""ISJ(大字・町丁目レベル)CSV読み取り(design §1.5・§4-2)。

fixtureはcp932で保存した合成データ(鹿児島市の実在町名・合成座標):
  伊敷町(市内・bbox内)/ 天文館一丁目(市内・bbox内)/ 大字草牟田(市内・bbox内)/
  中央町(市コード違い・代表点はbbox内 → ANDフィルタで除外)/
  松原町(市内・bbox外)/ 名山町(緯度欠損)/ 欠列町(列数不足)
"""

from pathlib import Path

import pytest

from latch.geo.ingest import BBox
from latch.geo.isj import iter_isj_towns

FIXTURE = Path(__file__).resolve().parents[2] / "fixtures" / "geo" / "isj_sample.csv"
DEFAULT_CODES = {"46201"}  # 鹿児島市(ci暫定エリアの既定値 — Task 1)
DEFAULT_BBOX = BBox.parse("130.5420,31.5825,130.5740,31.6095")


def _rows(**kwargs):
    defaults = {"city_codes": DEFAULT_CODES, "bbox": DEFAULT_BBOX}
    return list(iter_isj_towns(FIXTURE, **{**defaults, **kwargs}))


def test_reads_cp932_csv():
    rows = _rows()
    names = [r.name for r in rows]
    assert names == ["伊敷町", "天文館一丁目", "大字草牟田"]


def test_column_mapping():
    rows = _rows()
    iseki = rows[0]  # 伊敷町
    assert iseki.kind == "town"
    assert iseki.pref_name == "鹿児島県"
    assert iseki.city_name == "鹿児島市"
    assert (
        iseki.source_code == "46201001001"
    )  # 大字町丁目コード(11桁でよい・そのまま文字列)
    assert iseki.lat == pytest.approx(31.605000)
    assert iseki.lon == pytest.approx(130.552000)
    assert iseki.attrs["source_material_code"] == "2"
    assert iseki.attrs["town_class_code"] == "1"


def test_reads_utf8_csv(tmp_path):
    # Review Focus #1: UTF-8で配信された年度版も例外にならず読める(自動判定)
    raw = FIXTURE.read_bytes()
    try:
        raw.decode("utf-8")
        pytest.skip("fixtureがたまたまutf-8互換 — cp932前提のためskip")
    except UnicodeDecodeError:
        pass
    utf8 = tmp_path / "isj_utf8.csv"
    utf8.write_bytes(raw.decode("cp932").encode("utf-8"))
    rows = list(iter_isj_towns(utf8, city_codes=DEFAULT_CODES, bbox=DEFAULT_BBOX))
    assert [r.name for r in rows] == ["伊敷町", "天文館一丁目", "大字草牟田"]


def test_city_code_and_bbox_are_and_filter():
    # 中央町=市コード違い(代表点はbbox内)→除外。松原町=市内・bbox外→除外
    rows = _rows()
    assert "中央町" not in [r.name for r in rows]
    assert "松原町" not in [r.name for r in rows]


def test_invalid_rows_are_skipped():
    # 名山町=緯度欠損・欠列町=列数不足 → 例外ではなくスキップ(design §4-2)
    rows = _rows()
    assert "名山町" not in [r.name for r in rows]
    assert "欠列町" not in [r.name for r in rows]


def test_no_filters_returns_all_valid_rows():
    rows = _rows(city_codes=None, bbox=None)
    names = [r.name for r in rows]
    assert names == ["伊敷町", "天文館一丁目", "大字草牟田", "中央町", "松原町"]
    # 緯度欠損・列数不足の2行はフィルタなしでもスキップされる


class TestBBox:
    def test_parse_valid(self):
        b = BBox.parse("130.5420,31.5825,130.5740,31.6095")
        assert (b.min_lon, b.min_lat, b.max_lon, b.max_lat) == (
            130.5420,
            31.5825,
            130.5740,
            31.6095,
        )
        # Review Focus #2: 形式破損は明確なValueError(黙って通さない)
        for bad in ("130.5,31.5", "a,b,c,d", "1,2,3", "3,2,1,0", "1,2,1,2", ""):
            with pytest.raises(ValueError, match="bbox"):
                BBox.parse(bad)

    def test_contains(self):
        b = BBox.parse("130.5420,31.5825,130.5740,31.6095")
        assert b.contains(130.5585, 31.5965)
        assert not b.contains(139.7671, 35.6812)
