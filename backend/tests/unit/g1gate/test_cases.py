"""g1gate/cases.pyのunit(design §4-3)。docs実YAMLの構造回帰+異常系。"""

from pathlib import Path

import pytest
from pydantic import ValidationError

from latch.g1gate.cases import (
    BASE_CURRENT_DATETIME,
    load_alcohol,
    load_parser_struct,
)

REPO_ROOT = Path(__file__).resolve().parents[4]
DOCS_ASSETS = REPO_ROOT / "docs" / "testassets"
FIXTURES = Path(__file__).resolve().parents[2] / "fixtures" / "g1gate"


def test_loads_real_parser_struct_yaml():
    # docs実YAMLの構造回帰: 予期せぬ変更をここで検出する(10 §2 confirmed資産)
    parsed = load_parser_struct(DOCS_ASSETS / "g1-parser-struct.yaml")
    assert parsed.meta.status == "confirmed"
    assert len(parsed.cases) == 32
    assert len(parsed.error_cases) == 3
    ids = [c.id for c in parsed.cases]
    assert len(set(ids)) == 32  # id一意
    assert ids[0] == "P-001" and ids[-1] == "P-032"
    assert [c.id for c in parsed.error_cases] == ["E-001", "E-002", "E-003"]
    assert parsed.meta.current_datetime == BASE_CURRENT_DATETIME


def test_loads_real_parser_struct_expectation_shapes():
    parsed = load_parser_struct(DOCS_ASSETS / "g1-parser-struct.yaml")
    first = parsed.cases[0]
    assert first.expected.category == "drinking"
    assert first.expected.alcohol_involved is True
    assert first.expected.time.start == "2026-10-01T20:00:00+09:00"
    assert first.expected.location.name == "天文館"
    assert (first.expected.participants.min, first.expected.participants.max) == (
        None,
        None,
    )
    assert first.expected.budget.max == 3000


def test_loads_real_alcohol_yaml():
    parsed = load_alcohol(DOCS_ASSETS / "g1-alcohol.yaml")
    assert parsed.meta.status == "confirmed"
    assert len(parsed.cases) == 36
    true_count = sum(1 for c in parsed.cases if c.expected.alcohol_involved)
    assert true_count == 24  # true 24 / false 12(README)
    assert [c.id for c in parsed.cases][0] == "A-001"
    assert parsed.meta.current_datetime == BASE_CURRENT_DATETIME


def test_loads_fixture_minimal_sets():
    struct = load_parser_struct(FIXTURES / "struct-minimal.yaml")
    assert len(struct.cases) == 2 and len(struct.error_cases) == 1
    alcohol = load_alcohol(FIXTURES / "alcohol-minimal.yaml")
    assert len(alcohol.cases) == 3


def _rewrite(tmp_path, source: Path, old: str, new: str, name: str) -> Path:
    text = source.read_text(encoding="utf-8")
    assert old in text  # 置換対象が実在することの保証
    target = tmp_path / name
    target.write_text(text.replace(old, new), encoding="utf-8")
    return target


def test_rejects_wrong_current_datetime(tmp_path):
    path = _rewrite(
        tmp_path,
        FIXTURES / "struct-minimal.yaml",
        "2026-10-01T11:30:00+09:00",
        "2026-11-01T11:30:00+09:00",
        "wrong-datetime.yaml",
    )
    with pytest.raises(ValueError, match="current_datetime"):
        load_parser_struct(path)


def test_rejects_status_not_confirmed(tmp_path):
    path = _rewrite(
        tmp_path,
        FIXTURES / "struct-minimal.yaml",
        "status: confirmed",
        "status: draft",
        "draft.yaml",
    )
    with pytest.raises(ValueError, match="confirmed"):
        load_parser_struct(path)


def test_rejects_case_count_mismatch(tmp_path):
    path = _rewrite(
        tmp_path,
        FIXTURES / "struct-minimal.yaml",
        "case_count: 2",
        "case_count: 3",
        "count-mismatch.yaml",
    )
    with pytest.raises(ValueError, match="case_count"):
        load_parser_struct(path)


def test_rejects_duplicate_ids(tmp_path):
    path = _rewrite(
        tmp_path,
        FIXTURES / "struct-minimal.yaml",
        "- id: P-902",
        "- id: P-901",
        "dup-ids.yaml",
    )
    with pytest.raises(ValueError, match="id"):
        load_parser_struct(path)


def test_rejects_missing_expectation_field(tmp_path):
    path = _rewrite(
        tmp_path,
        FIXTURES / "struct-minimal.yaml",
        "      category: drinking",
        "",
        "no-category.yaml",
    )
    with pytest.raises(ValidationError):
        load_parser_struct(path)


def test_rejects_alcohol_true_count_mismatch(tmp_path):
    path = _rewrite(
        tmp_path,
        FIXTURES / "alcohol-minimal.yaml",
        "true_count: 2",
        "true_count: 1",
        "alcohol-mismatch.yaml",
    )
    with pytest.raises(ValueError, match="true_count"):
        load_alcohol(path)
