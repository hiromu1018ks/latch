"""g2gate部品(cases・compare・report・__main__)のunit試験(ws-8 design §2.8・§4.1)。

合成入力のみ(実APIなし・g1gateと同一規律)。指標は手計算既知値で検証する。
"""

import hashlib
from datetime import datetime
from pathlib import Path

import pytest
import yaml

from latch.core.clock import JST
from latch.g2gate.cases import (
    G2_BASE_CURRENT_DATETIME,
    load_goldset,
)

BASE = {
    "meta": {
        "status": "confirmed",
        "created": "2026-09-29",
        "current_datetime": "2026-10-01T11:30:00+09:00",
        "pair_count": 1,
        "intent_count": 2,
        "gold_true": 1,
        "gold_false": 0,
    },
    "intents": [
        {
            "id": "SI-001",
            "user": "U-001",
            "author_age": 34,
            "structured": {
                "category": {"primary": "drinking", "secondary": "居酒屋"},
                "alcohol_involved": True,
                "time": {
                    "start": "2026-10-01T20:00:00+09:00",
                    "end": "2026-10-01T23:00:00+09:00",
                },
                "location": {"name": "天文館", "radius_m": 1000},
                "budget": {"max": 3500},
                "participants": {"min": 2, "max": 2},
                "soft_constraints": ["軽く飲みたい"],
                "ng_unverifiable": ["会社関係の人は避けたい"],
            },
            "source": "drafted",
        },
        {
            "id": "SI-002",
            "user": "U-002",
            "author_age": 30,
            "structured": {
                "category": {"primary": "drinking"},
                "alcohol_involved": True,
                "time": {
                    "start": "2026-10-01T20:00:00+09:00",
                    "end": "2026-10-01T23:00:00+09:00",
                },
                "location": {"name": "天文館", "radius_m": 1000},
                "budget": {"max": 3000},
                "participants": {"min": 2, "max": 2},
                "soft_constraints": [],
                "ng_unverifiable": [],
            },
            "source": "machine",
        },
    ],
    "pairs": [
        {
            "id": "GP-001",
            "intent_a": "SI-001",
            "intent_b": "SI-002",
            "kind": "1to1",
            "segment": "lexical",
            "layer1_pass": True,
            "expected": {
                "gold_mutual": True,
                "would_a_accept_b": {"label": "accept", "band": "high"},
                "would_b_accept_a": {"label": "accept", "band": "high"},
                "purpose_fit": 4,
                "mood_fit": 3,
                "timing_fit": 4,
                "social_fit": 4,
                "latent_yes": {"band": "mid"},
            },
            "rationale": "test",
            "flag": False,
            "source": "drafted",
        }
    ],
}


def _write_goldset(tmp_path: Path, data: dict | None = None) -> Path:
    path = tmp_path / "g2-jev-goldset.yaml"
    path.write_text(
        yaml.safe_dump(data if data is not None else BASE, allow_unicode=True),
        encoding="utf-8",
    )
    return path


def test_load_goldset_returns_inputs_and_sha(tmp_path):
    g = load_goldset(_write_goldset(tmp_path))
    assert g.meta.status == "confirmed"
    assert (
        g.yaml_sha256
        == hashlib.sha256((tmp_path / "g2-jev-goldset.yaml").read_bytes()).hexdigest()
    )
    a = g.inputs["SI-001"]
    assert a.category_primary == "drinking"
    assert a.time_start == datetime(2026, 10, 1, 20, 0, tzinfo=JST)
    assert a.budget_max == 3500
    assert a.geo_radius_m == 1000
    assert g.expected["GP-001"].gold_mutual is True


def test_load_goldset_ng_unverifiable_becomes_downgraded_soft(tmp_path):
    g = load_goldset(_write_goldset(tmp_path))
    soft = g.inputs["SI-001"].structured_data["soft_constraints"]
    # 順序: {Falseの通常行} + {ng_unverifiable由来行}(引用#12・07 §4)
    assert soft == [
        {"text": "軽く飲みたい", "downgraded_from_ng": False},
        {"text": "会社関係の人は避けたい", "downgraded_from_ng": True},
    ]


def test_load_goldset_rejects_unconfirmed(tmp_path):
    data = yaml.safe_load(yaml.safe_dump(BASE, allow_unicode=True))
    data["meta"]["status"] = "draft"
    with pytest.raises(ValueError, match="confirmed"):
        load_goldset(_write_goldset(tmp_path, data))


def test_load_goldset_rejects_wrong_current_datetime(tmp_path):
    data = yaml.safe_load(yaml.safe_dump(BASE, allow_unicode=True))
    data["meta"]["current_datetime"] = "2026-10-02T11:30:00+09:00"
    with pytest.raises(ValueError, match="current_datetime"):
        load_goldset(_write_goldset(tmp_path, data))


def test_load_goldset_rejects_count_mismatch(tmp_path):
    data = yaml.safe_load(yaml.safe_dump(BASE, allow_unicode=True))
    data["meta"]["pair_count"] = 2
    with pytest.raises(ValueError, match="pair_count"):
        load_goldset(_write_goldset(tmp_path, data))


def test_load_goldset_rejects_gold_true_mismatch(tmp_path):
    data = yaml.safe_load(yaml.safe_dump(BASE, allow_unicode=True))
    data["meta"]["gold_true"] = 0
    with pytest.raises(ValueError, match="gold"):
        load_goldset(_write_goldset(tmp_path, data))


def test_load_goldset_rejects_unknown_pair_reference(tmp_path):
    data = yaml.safe_load(yaml.safe_dump(BASE, allow_unicode=True))
    data["pairs"][0]["intent_b"] = "SI-999"
    with pytest.raises(ValueError, match="SI-999"):
        load_goldset(_write_goldset(tmp_path, data))


def test_base_current_datetime_pin():
    assert G2_BASE_CURRENT_DATETIME == datetime(2026, 10, 1, 11, 30, tzinfo=JST)
    assert G2_BASE_CURRENT_DATETIME == datetime.fromisoformat(
        "2026-10-01T11:30:00+09:00"
    )
