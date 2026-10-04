"""massload行組み立て純関数の期待値(M4 ws-6・design §2.4案A)。

実DBのINSERT・active条件照合はintegration(test_loadgen_massload_ci.py)が
所有する。unitでは決定性・structured_dataの保存形式・embeddingの決定生成・
match_events非触りの静的検証を行う(DB不使用)。
"""

import uuid
from datetime import UTC, datetime

import pytest

from latch.llm.gemini import GEMINI_EMBEDDING_MODEL
from latch.llm.stub import _stub_vector
from latch.loadgen import massload
from latch.worker.embedding_text import EmbeddingTextInput, build_embedding_text

NOW = datetime(2026, 10, 4, 3, 0, 0, tzinfo=UTC)


@pytest.fixture(scope="module")
def seed() -> dict:
    """決定的生成器の小さい実出力(count=70=EX64+残余6・DB不要)。"""
    from latch.testassets.generator import build_seed

    return build_seed(datetime(2026, 10, 4, 2, 0, 0), count=70)


def _test_ids_map(seed) -> dict:
    """seed全userのsubject→idマップ(seed intentsは複数userに渡るため・
    決定的生成の実user一覧から組む)。"""
    return {f"massload-{u['id']}": uuid.uuid4() for u in seed["users"]}


def test_to_user_rows_uses_load_prefix_and_passes_seed_fields(seed):
    rows = massload.to_user_rows(seed, NOW)
    assert len(rows) == 100  # generatorのusersは100人固定
    first = rows[0]
    assert first[0] == seed["users"][0]["display_name"]  # display_name
    assert first[3].startswith("massload-")  # auth_subjectはmassload接頭辞
    assert first[4] == NOW  # consent_at・created_at・updated_atは同一now


def test_flat_structured_matches_saved_shape(seed):
    """平らな保存形式(mapping.pyと同型の5キー+location_name)。"""
    i = seed["intents"][0]
    sd = massload.FLAT_STRUCTURED(i)
    assert set(sd.keys()) == {
        "category_secondary",
        "soft_constraints",
        "negative_constraints",
        "time_flexibility_minutes",
        "location_flexibility",
        "location_name",
    }
    assert sd["negative_constraints"] == []
    for c in sd["soft_constraints"]:
        assert set(c.keys()) == {"text", "downgraded_from_ng"}


def test_user_ids_map_is_required_for_intents(seed):
    """intents行はuser_idを引くため・users投入後のsubject→idマップを要求する。"""
    ids = _test_ids_map(seed)
    rows = massload.to_intent_rows(seed, ids, NOW)
    assert len(rows) == len(seed["intents"])
    first = rows[0]
    first_subject = f"massload-{seed['intents'][0]['user']}"
    assert first[1] == ids[first_subject]


def test_intent_rows_are_deterministic(seed):
    ids = _test_ids_map(seed)
    rows_a = massload.to_intent_rows(seed, ids, NOW)
    rows_b = massload.to_intent_rows(seed, ids, NOW)
    assert rows_a == rows_b
    # idは決定的uuid5(再投入べき等の根拠)
    assert rows_a[0][0] == uuid.uuid5(massload.MASSLOAD_NS, seed["intents"][0]["id"])


def test_intent_rows_embedding_matches_stub_vector(seed):
    """embedding列=_stub_vector(正規化テキスト)・768次元・model=既存の
    スタブ系記録と同一値(design §2.4)。列は文字列化済みのvectorリテラル。"""
    i = seed["intents"][0]
    rows = massload.to_intent_rows(seed, _test_ids_map(seed), NOW)
    row = rows[0]
    s = i["structured"]
    expected_text = build_embedding_text(
        EmbeddingTextInput(
            category_primary=s["category"]["primary"],
            structured_data=massload.FLAT_STRUCTURED(i),
            participants_min=s["participants"]["min"],
            participants_max=s["participants"]["max"],
            time_start=rows[0][14],
            time_end=rows[0][15],
        )
    )
    expected_vec = _stub_vector(expected_text)
    expected_literal = "[" + ",".join(repr(float(v)) for v in expected_vec) + "]"
    assert row[17] == expected_literal  # embedding列(位置17)
    assert row[17].startswith("[") and row[17].endswith("]")
    assert row[18] == GEMINI_EMBEDDING_MODEL  # model列(位置18)


def test_intent_rows_active_conditions(seed):
    """active条件の列: expires_at未来・geoあり・time列あり。status='active'は
    INSERT SQLのリテラル(静的ピン)・§2.2(c)。"""
    ids = _test_ids_map(seed)
    rows = massload.to_intent_rows(seed, ids, NOW)
    for row in rows:
        assert row[16] > NOW  # expires_at(位置16)
        assert row[6] is not None and row[7] is not None  # geo lon/lat
        assert row[14] is not None  # time_start
    assert "'active'" in massload.INSERT_INTENT  # statusはSQLリテラル
    assert "consent_at" in massload.INSERT_USER  # Layer 1対象条件


def test_insert_statements_never_touch_match_events():
    """母集合投入はEvent発行しない(match_eventsへのINSERTなし・design §2.4)。"""
    for sql in (massload.INSERT_USER, massload.SELECT_USER_IDS, massload.INSERT_INTENT):
        assert "match_events" not in sql


def test_insert_user_uses_google_provider_and_consent():
    """auth_providerはCHECK制約('google','apple')上'google'でINSERT・consent_at設定。"""
    sql = massload.INSERT_USER
    assert "'google'" in sql
    assert "consent_at" in sql
    assert "ON CONFLICT (auth_provider, auth_subject) DO NOTHING" in sql


def test_insert_intent_is_idempotent_on_id():
    """intentsは決定的idでON CONFLICT DO NOTHING(段階間の累積投入)。"""
    assert "ON CONFLICT (id) DO NOTHING" in massload.INSERT_INTENT
    assert "ST_SetSRID(ST_MakePoint" in massload.INSERT_INTENT  # geo_center直書き
