"""02#9 Hard Filter 単体試験(M2 ws-3 design §4.2)。

実DB(compose常設DB・PostGIS/pgvector実物)。Workerは起動しない
(直接関数呼び出し・FakeClock注入)。Embedding実体(ws-2)に依存しない:
APIでactive Intentを作り、embeddingは768次元ベクトルを直接UPDATEで挿入。
teardownはsubjectプレフィックス単位で match_candidates → match_events →
intents → blocks → users の順に削除(design §4.2・FK順)。
19歳×飲酒ペアはAPIの20歳検証(_require_age_20)をバイパスするため
DB直接UPDATEで作出(06 §2「API作成・更新時検証に対する二重防御」の実試験)。
"""

import asyncio
import sys
import uuid as uuid_mod
from datetime import date, datetime, timedelta

import pytest
from sqlalchemy import text

from latch.core.clock import JST, FakeClock, SystemClock
from latch.worker.matching import (
    hard_filter_candidates,
    load_origin,
    run_candidate_retrieval,
)
from latch.worker.matching.origin import SKIP_PARTICIPANTS

pytestmark = pytest.mark.integration

# 緯度1度≈111,320m(南北方向のみの移動で距離を制御。WGS84楕円体でも
# 誤差は数m — 判定マージン(数百m)に対して無視できる)
METERS_PER_DEG_LAT = 111_320.0


# -- 共通ヘルパ(test_events_pipeline.py と同じ流儀) --


async def _cli_idp_token(provider: str, subject: str) -> str:
    proc = await asyncio.create_subprocess_exec(
        sys.executable,
        "-m",
        "latch.auth",
        "issue-idp-token",
        "--provider",
        provider,
        "--subject",
        subject,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    stdout, stderr = await proc.communicate()
    assert proc.returncode == 0, stderr.decode()
    return stdout.decode().strip()


async def _register(api_client, subject: str, birth_date: str):
    idp_token = await _cli_idp_token("google", subject)
    tok = await api_client.post(
        "/v1/auth/token", json={"provider": "google", "idp_token": idp_token}
    )
    assert tok.status_code == 200, tok.text
    headers = {"Authorization": f"Bearer {tok.json()['access_token']}"}
    created = await api_client.post(
        "/v1/users",
        headers=headers,
        json={"display_name": "m2ws3", "birth_date": birth_date, "profile": {}},
    )
    assert created.status_code == 201, created.text
    return headers, created.json()["user"]["id"]


def _vec(*components: float) -> str:
    """768次元ベクトル文字列(先頭要素のみ値・残り0。直交/平行の制御用)。"""
    vals = [0.0] * 768
    for i, c in enumerate(components):
        vals[i] = c
    return "[" + ",".join(repr(v) for v in vals) + "]"


E1 = _vec(1.0)  # 起点・対照の標準ベクトル(E2=_vec(0.0,1.0) と直交)


def _future(hours: float) -> str:
    now = SystemClock().now()
    return (now + timedelta(hours=hours)).isoformat()


def _birth_jst_years_ago(years: int, *, plus_days: int = 0) -> str:
    """JST今日基準で満 years 歳になる誕生日。plus_days=1 は誕生日前日
    (=まだ満 years-1 歳)。うるう日起点は3/2へ外す(PostgreSQLのAGEは
    2/29生まれの繰り上げを暦減算で行いPythonと最大1ヶ月ずれるため、
    境界試験の対象から外す — 本計画§9)。"""
    today = SystemClock().now().astimezone(JST).date()
    try:
        d = today.replace(year=today.year - years)
    except ValueError:  # 2/29生まれ相当
        d = today.replace(year=today.year - years, day=28)
    if (d.month, d.day) in {(2, 28), (2, 29), (3, 1)}:
        d = date(d.year, 3, 2)
    return (d + timedelta(days=plus_days)).isoformat()


def _structured(
    *,
    category: str = "drinking",
    start: str | None = None,
    budget: int | None = None,
    participants: tuple[int, int] | None = None,
    visibility: str | None = None,
    alcohol: bool = False,
) -> dict:
    d: dict = {
        "category": {"primary": category, "secondary": None},
        "alcohol_involved": alcohol,
        "time": {"start": start or _future(3), "end": None},
        "location": {"name": "天文館"},
    }
    if budget is not None:
        d["budget"] = {"max": budget}
    if participants is not None:
        d["participants"] = {"min": participants[0], "max": participants[1]}
    if visibility is not None:
        d["visibility"] = visibility
    return d


def _payload(structured: dict, *, status: str = "active") -> dict:
    return {
        "raw_text": "今夜 天文館で飲みたい",
        "status": status,
        "structured_intent": structured,
    }


async def _create(api_client, headers, payload) -> dict:
    resp = await api_client.post("/v1/intents", headers=headers, json=payload)
    assert resp.status_code == 201, resp.text
    return resp.json()["intent"]


async def _set(db_engine, intent_id: str, sql_set: str) -> None:
    async with db_engine.begin() as conn:
        await conn.execute(
            text(f"UPDATE intents SET {sql_set} WHERE id = CAST(:iid AS uuid)"),
            {"iid": intent_id},
        )


async def _intent(
    api_client, db_engine, headers, payload, *, vec: str | None = E1
) -> dict:
    """active Intentを作りembeddingを直接挿入(design §4.2のfixture方針)。"""
    intent = await _create(api_client, headers, payload)
    if vec is not None:
        await _set(
            db_engine,
            intent["id"],
            "embedding = CAST('" + vec + "' AS vector), embedding_model = 'fixture'",
        )
    return intent


async def _geo(db_engine, intent_id: str) -> tuple[float, float]:
    async with db_engine.connect() as conn:
        row = (
            await conn.execute(
                text(
                    "SELECT ST_X(geo_center::geometry), ST_Y(geo_center::geometry)"
                    " FROM intents WHERE id = CAST(:iid AS uuid)"
                ),
                {"iid": intent_id},
            )
        ).first()
    assert row is not None
    return float(row[0]), float(row[1])


async def _hard_ids(db_engine, clock, origin_intent_id: str) -> set[str]:
    """Layer 1 通過集合のintent_id一覧(02#9の判定面)。"""
    async with db_engine.connect() as conn:
        loaded = await load_origin(conn, clock, uuid_mod.UUID(origin_intent_id))
        assert loaded.origin is not None, loaded.skip_reason
        rows = await hard_filter_candidates(conn, loaded.origin)
    return {str(c.intent_id) for c in rows}


async def _run(db_engine, clock, origin_intent_id: str):
    async with db_engine.begin() as conn:
        return await run_candidate_retrieval(
            conn, clock, uuid_mod.UUID(origin_intent_id)
        )


@pytest.fixture
async def field(db_engine):
    """試験ごとに一意のsubjectプレフィックス。teardownでFK順に全削除。"""
    prefix = f"m2ws3-{uuid_mod.uuid4().hex[:8]}-"
    yield prefix
    p = {"p": prefix + "%"}
    async with db_engine.begin() as conn:
        await conn.execute(
            text(
                "DELETE FROM match_candidates WHERE intent_a_id IN"
                " (SELECT id FROM intents WHERE user_id IN"
                "  (SELECT id FROM users WHERE auth_subject LIKE :p))"
                " OR intent_b_id IN"
                " (SELECT id FROM intents WHERE user_id IN"
                "  (SELECT id FROM users WHERE auth_subject LIKE :p))"
            ),
            p,
        )
        await conn.execute(
            text(
                "DELETE FROM match_events WHERE source_intent_id IN"
                " (SELECT id FROM intents WHERE user_id IN"
                "  (SELECT id FROM users WHERE auth_subject LIKE :p))"
            ),
            p,
        )
        await conn.execute(
            text(
                "DELETE FROM intents WHERE user_id IN"
                " (SELECT id FROM users WHERE auth_subject LIKE :p)"
            ),
            p,
        )
        await conn.execute(
            text(
                "DELETE FROM blocks WHERE blocker_id IN"
                " (SELECT id FROM users WHERE auth_subject LIKE :p)"
                " OR blocked_id IN (SELECT id FROM users WHERE auth_subject LIKE :p)"
            ),
            p,
        )
        await conn.execute(text("DELETE FROM users WHERE auth_subject LIKE :p"), p)


async def _user(api_client, prefix: str, birth_date: str = "1990-04-01"):
    subject = f"{prefix}{uuid_mod.uuid4().hex[:8]}"
    return await _register(api_client, subject, birth_date)


def _clock() -> FakeClock:
    return FakeClock(SystemClock().now())


# -- §4.2 試験1〜5 + Review Focus + design §5-3 --


async def test_1_budget_conditions(api_client, db_engine, field):
    """02#9予算系統: ペア予算499 fail(片方・双方)・NULL系/500はfailしない。"""
    clock = _clock()
    h_on, _ = await _user(api_client, field)
    o_null = await _intent(api_client, db_engine, h_on, _payload(_structured()))
    h_o499, _ = await _user(api_client, field)
    o_499 = await _intent(
        api_client, db_engine, h_o499, _payload(_structured(budget=499))
    )
    h1, _ = await _user(api_client, field)
    t_null = await _intent(api_client, db_engine, h1, _payload(_structured()))
    h2, _ = await _user(api_client, field)
    t_499 = await _intent(api_client, db_engine, h2, _payload(_structured(budget=499)))
    h3, _ = await _user(api_client, field)
    t_500 = await _intent(api_client, db_engine, h3, _payload(_structured(budget=500)))

    ids = await _hard_ids(db_engine, clock, o_null["id"])
    assert t_null["id"] in ids  # NULL×NULL → 制約なし → failしない(06 §2)
    assert t_499["id"] not in ids  # NULL×499 = ペア予算499 → fail
    assert t_500["id"] in ids  # NULL×500 → 500未満でない → pass(境界)

    # 起点499はどの対象とも LEAST(499, x) <= 499 になる(500ちょうどの
    # 境界passは o_null 側の検証で担保済み — Review Focus 3)
    ids499 = await _hard_ids(db_engine, clock, o_499["id"])
    assert t_null["id"] not in ids499  # 499×NULL = 499 → fail(片方499の逆側)
    assert t_499["id"] not in ids499  # 499×499 = 499 → fail(双方499)
    assert t_500["id"] not in ids499  # 499×500 = LEAST(499,500) = 499 → fail


async def test_2_time_intersection(api_client, db_engine, field):
    """02#9時間系統: 半開区間 [s,e) の交差なし・境界接触(e_a=s_b)はfail。"""
    clock = _clock()
    h, _ = await _user(api_client, field)
    o = await _intent(api_client, db_engine, h, _payload(_structured()))
    origin_end = datetime.fromisoformat(o["structured_intent"]["time"]["end"])
    h1, _ = await _user(api_client, field)
    t_pass = await _intent(
        api_client, db_engine, h1, _payload(_structured(start=_future(4)))
    )
    h2, _ = await _user(api_client, field)
    t_touch = await _intent(
        api_client,
        db_engine,
        h2,
        _payload(_structured(start=origin_end.isoformat())),  # e_a = s_b
    )
    h3, _ = await _user(api_client, field)
    t_no = await _intent(
        api_client, db_engine, h3, _payload(_structured(start=_future(7)))
    )

    ids = await _hard_ids(db_engine, clock, o["id"])
    assert t_pass["id"] in ids  # [3,6) と [4,7) は交差
    assert t_touch["id"] not in ids  # [6,9) と [3,6) の境界接触=交差なし
    assert t_no["id"] not in ids  # [7,10) は交差なし


async def test_3_distance_radius_sum(api_client, db_engine, field):
    """02#9距離系統: r_a + r_b の外れはfail・和の内側(0.9×和)は通過。"""
    clock = _clock()
    h, _ = await _user(api_client, field)
    o = await _intent(api_client, db_engine, h, _payload(_structured()))
    lon, lat = await _geo(db_engine, o["id"])
    # 既定 r_a = r_b = 1000m(completion.DEFAULT_RADIUS_M)→ 和 = 2000m
    h1, _ = await _user(api_client, field)
    t_in = await _intent(api_client, db_engine, h1, _payload(_structured()))
    await _set(
        db_engine,
        t_in["id"],
        "geo_center = ST_SetSRID(ST_MakePoint("
        f"CAST({lon!r} AS float8),"
        f" CAST({lat + 1800 / METERS_PER_DEG_LAT!r} AS float8)"
        "), 4326)::geography",  # 1.8km < 2km → 通過
    )
    h2, _ = await _user(api_client, field)
    t_out = await _intent(api_client, db_engine, h2, _payload(_structured()))
    await _set(
        db_engine,
        t_out["id"],
        "geo_center = ST_SetSRID(ST_MakePoint("
        f"CAST({lon!r} AS float8),"
        f" CAST({lat + 3000 / METERS_PER_DEG_LAT!r} AS float8)"
        "), 4326)::geography",  # 3km > 2km → fail
    )

    ids = await _hard_ids(db_engine, clock, o["id"])
    assert t_in["id"] in ids
    assert t_out["id"] not in ids


async def test_4_participants(api_client, db_engine, field):
    """02#9人数系統: 対象 max=1 はfail・起点 min=3 はno-op(§2.5)。"""
    clock = _clock()
    h, _ = await _user(api_client, field)
    o = await _intent(api_client, db_engine, h, _payload(_structured()))
    h1, _ = await _user(api_client, field)
    t_p1 = await _intent(
        api_client, db_engine, h1, _payload(_structured(participants=(1, 1)))
    )
    h2, _ = await _user(api_client, field)
    t_pass = await _intent(api_client, db_engine, h2, _payload(_structured()))
    h3, _ = await _user(api_client, field)
    o_p3 = await _intent(
        api_client, db_engine, h3, _payload(_structured(participants=(3, 4)))
    )

    ids = await _hard_ids(db_engine, clock, o["id"])
    assert t_p1["id"] not in ids  # 2 ∉ [1,1] → fail
    assert t_pass["id"] in ids  # 2 ∈ [2,2](既定) → 通過
    # 起点側 min=3 → 検索前にno-op(design §2.5)
    async with db_engine.connect() as conn:
        loaded = await load_origin(conn, clock, uuid_mod.UUID(o_p3["id"]))
    assert loaded.origin is None and loaded.skip_reason == SKIP_PARTICIPANTS


async def test_5_blocks_both_directions(api_client, db_engine, field):
    """02#9ブロック系統: (A,B)(B,A)いずれの向きもfail(06 §2)。"""
    clock = _clock()
    h_o, o_user = await _user(api_client, field)
    o = await _intent(api_client, db_engine, h_o, _payload(_structured()))
    h1, ta_user = await _user(api_client, field)
    t_a = await _intent(api_client, db_engine, h1, _payload(_structured()))
    h2, tb_user = await _user(api_client, field)
    t_b = await _intent(api_client, db_engine, h2, _payload(_structured()))
    async with db_engine.begin() as conn:
        await conn.execute(
            text(
                "INSERT INTO blocks (blocker_id, blocked_id, created_at)"
                " VALUES (CAST(:b AS uuid), CAST(:d AS uuid), :now)"
            ),
            {"b": o_user, "d": ta_user, "now": SystemClock().now()},  # O→A
        )
        await conn.execute(
            text(
                "INSERT INTO blocks (blocker_id, blocked_id, created_at)"
                " VALUES (CAST(:b AS uuid), CAST(:d AS uuid), :now)"
            ),
            {"b": tb_user, "d": o_user, "now": SystemClock().now()},  # B→O
        )

    ids = await _hard_ids(db_engine, clock, o["id"])
    assert t_a["id"] not in ids  # 起点が対象をブロック
    assert t_b["id"] not in ids  # 対象が起点をブロック


async def test_6_category_mismatch(api_client, db_engine, field):
    """02#9カテゴリ系統: category_primary完全一致のみ(secondaryは対象外)。"""
    clock = _clock()
    h, _ = await _user(api_client, field)
    o = await _intent(api_client, db_engine, h, _payload(_structured()))
    h1, _ = await _user(api_client, field)
    t_cat = await _intent(
        api_client, db_engine, h1, _payload(_structured(category="activity"))
    )
    ids = await _hard_ids(db_engine, clock, o["id"])
    assert t_cat["id"] not in ids


async def test_7_alcohol_underage(api_client, db_engine, field):
    """02#9飲酒年齢系統: 19歳×20歳の飲酒ペアは双方向きともfail。

    19歳の alcohol_involved=true はAPIの20歳検証(_require_age_20)を
    通らないためDB直接UPDATEで強制する(06 §2の二重防御の実試験)。
    """
    clock = _clock()
    h20, _ = await _user(api_client, field)
    o20 = await _intent(api_client, db_engine, h20, _payload(_structured(alcohol=True)))
    h1, _ = await _user(api_client, field)
    t20 = await _intent(api_client, db_engine, h1, _payload(_structured(alcohol=True)))
    h2, _ = await _user(api_client, field, _birth_jst_years_ago(19))
    # 19歳×drinkingはAPIのサーバ側確定(M1・07 §2)で422になるため、
    # 非飲酒カテゴリで作成してからカテゴリ・alcohol両方をDB直接変更する(API検証バイパス)
    o19 = await _intent(
        api_client, db_engine, h2, _payload(_structured(category="meal"))
    )
    await _set(
        db_engine, o19["id"], "category_primary = 'drinking', alcohol_involved = true"
    )
    h3, _ = await _user(api_client, field, _birth_jst_years_ago(19))
    t19 = await _intent(
        api_client, db_engine, h3, _payload(_structured(category="meal"))
    )
    await _set(
        db_engine, t19["id"], "category_primary = 'drinking', alcohol_involved = true"
    )

    ids = await _hard_ids(db_engine, clock, o20["id"])
    assert t20["id"] in ids  # 20歳×20歳の飲酒ペアは通過
    assert t19["id"] not in ids  # 対象19歳 → fail
    ids19 = await _hard_ids(db_engine, clock, o19["id"])
    assert t20["id"] not in ids19  # 起点19歳 → fail(起点側の20歳不成立)


async def test_8_age_boundary_jst(api_client, db_engine, field):
    """design §5-3: PostgreSQL AGE の満年齢ピン(誕生日当日=20歳・前日=19歳)。"""
    clock = _clock()
    h20, _ = await _user(api_client, field)
    o20 = await _intent(api_client, db_engine, h20, _payload(_structured(alcohol=True)))
    # 今日が20歳の誕生日(JST暦日)→ 当日=満20歳 → 通過
    # (19歳相当はdrinking作成が422になるためmeal作成→DBで両方強制。test_7と同じ手法)
    hb, _ = await _user(api_client, field, _birth_jst_years_ago(20))
    t_bday = await _intent(
        api_client, db_engine, hb, _payload(_structured(category="meal"))
    )
    await _set(
        db_engine, t_bday["id"], "category_primary = 'drinking', alcohol_involved = true"
    )
    # 誕生日は明日(=19歳)→ fail
    hc, _ = await _user(api_client, field, _birth_jst_years_ago(20, plus_days=1))
    t_eve = await _intent(
        api_client, db_engine, hc, _payload(_structured(category="meal"))
    )
    await _set(
        db_engine, t_eve["id"], "category_primary = 'drinking', alcohol_involved = true"
    )

    ids = await _hard_ids(db_engine, clock, o20["id"])
    assert t_bday["id"] in ids  # EXTRACT(YEAR FROM AGE(...)) = 20
    assert t_eve["id"] not in ids  # = 19


async def test_9_visibility_not_filtered(api_client, db_engine, field):
    """06 §2: visibilityはLayer 1の判定対象外(2値とも通過)。"""
    clock = _clock()
    h, _ = await _user(api_client, field)
    o = await _intent(api_client, db_engine, h, _payload(_structured()))
    h1, _ = await _user(api_client, field)
    t_hidden = await _intent(
        api_client,
        db_engine,
        h1,
        _payload(_structured(visibility="hidden_until_match")),
    )
    h2, _ = await _user(api_client, field)
    t_summary = await _intent(
        api_client, db_engine, h2, _payload(_structured(visibility="summary_only"))
    )
    ids = await _hard_ids(db_engine, clock, o["id"])
    assert t_hidden["id"] in ids and t_summary["id"] in ids


async def test_10_self_exclusion(api_client, db_engine, field):
    """FR-17(引用#12): 同一ユーザーの2 Intent(同一時間帯・地域・カテゴリ・
    embedding同一)がいずれの処理でも候補にならない(hardfilter・run両面)。"""
    clock = _clock()
    h, _ = await _user(api_client, field)
    o = await _intent(api_client, db_engine, h, _payload(_structured()))
    own2 = await _intent(api_client, db_engine, h, _payload(_structured()))

    ids = await _hard_ids(db_engine, clock, o["id"])
    assert own2["id"] not in ids  # 自己除外
    outcome = await _run(db_engine, clock, o["id"])
    assert outcome.pairs == []  # 対象が自己のみ → 1行も生成されない


async def test_11_defense_coalesce_targets_pass(api_client, db_engine, field):
    """Review Focus 1・2: 対象 time_end NULL / geo_radius_m NULL の防御COALESCE。"""
    clock = _clock()
    h, _ = await _user(api_client, field)
    o = await _intent(api_client, db_engine, h, _payload(_structured()))
    h1, _ = await _user(api_client, field)
    t_noend = await _intent(
        api_client, db_engine, h1, _payload(_structured(start=_future(4)))
    )
    await _set(db_engine, t_noend["id"], "time_end = NULL")
    h2, _ = await _user(api_client, field)
    t_norad = await _intent(api_client, db_engine, h2, _payload(_structured()))
    await _set(db_engine, t_norad["id"], "geo_radius_m = NULL")

    ids = await _hard_ids(db_engine, clock, o["id"])
    # COALESCE(time_end, start+3h)=f(7) と起点 [3,6) が交差 → 通過
    assert t_noend["id"] in ids
    # COALESCE(geo_radius_m, 1000) で距離和2000m・同じ地点 → 通過
    assert t_norad["id"] in ids


async def test_12_draft_target_excluded(api_client, db_engine, field):
    """06 §3: draft対象は他条件が揃っていても対象外(status='active'条件)。

    draft行はジオコーディングが走らないためgeo_centerのみ直接UPDATEで
    他条件を成立させる(時間はresolve_for_draftがstructuredから保存)。
    それでも除外されればstatusによる除外の実証になる。
    """
    clock = _clock()
    h, _ = await _user(api_client, field)
    o = await _intent(api_client, db_engine, h, _payload(_structured()))
    h1, _ = await _user(api_client, field)
    t_draft = await _intent(
        api_client, db_engine, h1, _payload(_structured(), status="draft")
    )
    lon, lat = await _geo(db_engine, o["id"])
    await _set(
        db_engine,
        t_draft["id"],
        "geo_center = ST_SetSRID(ST_MakePoint("
        f"CAST({lon!r} AS float8), CAST({lat!r} AS float8)), 4326)::geography",
    )
    ids = await _hard_ids(db_engine, clock, o["id"])
    assert t_draft["id"] not in ids


async def test_13_run_retrieval_excludes_fail_pairs(api_client, db_engine, field):
    """02#9受け入れ形: run_candidate_retrieval 経由でもfail対象は
    match_candidates に生成されない(pass対照のみ1行・status='pending')。"""
    clock = _clock()
    h, _ = await _user(api_client, field)
    o = await _intent(api_client, db_engine, h, _payload(_structured()))
    await _intent(api_client, db_engine, h, _payload(_structured()))  # 自己2つ目
    h1, _ = await _user(api_client, field)
    t_pass = await _intent(api_client, db_engine, h1, _payload(_structured()))
    h2, _ = await _user(api_client, field)
    await _intent(api_client, db_engine, h2, _payload(_structured(category="activity")))
    h3, _ = await _user(api_client, field)
    await _intent(api_client, db_engine, h3, _payload(_structured(budget=499)))

    outcome = await _run(db_engine, clock, o["id"])
    assert outcome.skip_reason is None and outcome.version == 1
    assert outcome.layer1_pass_count == 1  # pass対照のみ
    assert len(outcome.pairs) == 1
    pair = outcome.pairs[0]
    assert str(min(uuid_mod.UUID(o["id"]), uuid_mod.UUID(t_pass["id"]))) == str(
        pair.intent_a_id
    )  # 正規化 a<b
    async with db_engine.connect() as conn:
        rows = (
            await conn.execute(
                text(
                    "SELECT intent_a_id, intent_b_id, status, retrieval_score"
                    " FROM match_candidates"
                    " WHERE intent_a_id = CAST(:o AS uuid)"
                    " OR intent_b_id = CAST(:o AS uuid)"
                ),
                {"o": o["id"]},
            )
        ).all()
    assert len(rows) == 1
    assert rows[0][2] == "pending"  # Layer 1〜2時点(05 §2)
    assert float(rows[0][3]) == pytest.approx(1.0)  # E1×E1 = cosine 1
