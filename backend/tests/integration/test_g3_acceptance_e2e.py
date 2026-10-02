"""G3受け入れE2Eのintegration試験(M3 ws-9 design §2.3・12 §3 M3のG3条件1〜3)。

実DB(compose常設)・実Redis・api常設(実HTTP)。新設6試験は既存試験が
持ち切れないG3文言の一気通貫分(02#13のmuted回答可能チェーン・02#22の
公開設定2値A/B比較+02#23座標非開示・09 §3の期限確定/競合クローズ
グループ成立側/部分成立defer側・FR-17自己重複集合)。ヘルパーは
test_notifications_api・test_latches_api・test_expiry_batches・
test_matching_groupengineの確立形式の自前複製(共通化リファクタは
既存試験への波及が大きく本単位外 — design §2.2)。
実行はスーパーバイザー検証時のmake test-ci(STATUS運用ルール1〜3)。
"""

import asyncio
import json
import logging
import sys
import uuid as uuid_mod
from datetime import datetime, timedelta

import pytest
from sqlalchemy import text

from latch.core.clock import FakeClock, SystemClock
from latch.geo.service import GeoService
from latch.notifications.records import LOGGER_NAME
from latch.notifications.sender import StubPushSender
from latch.worker.matching.group_engine import GroupEngine
from latch.worker.matching.latch_engine import LatchEngine
from latch.worker.matching.runner import run_candidate_retrieval
from latch.worker.sweeper import ExpirySweeper

pytestmark = pytest.mark.integration

CATEGORY = "meal"
BASE_HOURS = 120  # now+5日系(既存単位と同一の対抗策・時間窓分離)
FAR_EXPIRES_H = 144
SUBJECT_PREFIX = "m3ws9-"


@pytest.fixture
async def field(db_engine):
    """試験ごとに一意のsubjectプレフィックス。teardownでFK順に全削除。"""
    prefix = f"{SUBJECT_PREFIX}{uuid_mod.uuid4().hex[:8]}-"
    yield prefix
    p = {"p": prefix + "%"}
    async with db_engine.begin() as conn:
        for sql in (
            "DELETE FROM latch_status_events WHERE latch_id IN"
            " (SELECT id FROM latches WHERE intent_ids && ARRAY("
            "  SELECT id FROM intents WHERE user_id IN"
            "  (SELECT id FROM users WHERE auth_subject LIKE :p)))",
            "DELETE FROM calibration_records WHERE latch_id IN"
            " (SELECT id FROM latches WHERE intent_ids && ARRAY("
            "  SELECT id FROM intents WHERE user_id IN"
            "  (SELECT id FROM users WHERE auth_subject LIKE :p)))",
            "DELETE FROM notifications WHERE user_id IN"
            " (SELECT id FROM users WHERE auth_subject LIKE :p)",
            "DELETE FROM latches WHERE intent_ids && ARRAY("
            "  SELECT id FROM intents WHERE user_id IN"
            "  (SELECT id FROM users WHERE auth_subject LIKE :p))",
            "DELETE FROM match_candidates WHERE intent_a_id IN"
            " (SELECT id FROM intents WHERE user_id IN"
            "  (SELECT id FROM users WHERE auth_subject LIKE :p))"
            " OR intent_b_id IN"
            " (SELECT id FROM intents WHERE user_id IN"
            "  (SELECT id FROM users WHERE auth_subject LIKE :p))",
            "DELETE FROM group_candidates WHERE intent_ids && ARRAY("
            "  SELECT id FROM intents WHERE user_id IN"
            "  (SELECT id FROM users WHERE auth_subject LIKE :p))",
            "DELETE FROM match_events WHERE source_intent_id IN"
            " (SELECT id FROM intents WHERE user_id IN"
            "  (SELECT id FROM users WHERE auth_subject LIKE :p))",
            "DELETE FROM intents WHERE user_id IN"
            " (SELECT id FROM users WHERE auth_subject LIKE :p)",
            "DELETE FROM users WHERE auth_subject LIKE :p",
        ):
            await conn.execute(text(sql), p)


# -- ヘルパー(4ファイルの確立形式の自前複製・design §2.2) --


async def _cli_idp_token(provider: str, subject: str) -> str:
    """テストIdPでJWT発行(認証サービスのci構成・各integrationファイルが
    自前定義する流儀 — tests配下に__init__.pyがないためimport不可)。"""
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


async def _user(api_client, prefix: str, birth_date: str = "1990-04-01"):
    subject = f"{prefix}{uuid_mod.uuid4().hex[:8]}"
    idp = await _cli_idp_token("google", subject)
    tok = await api_client.post(
        "/v1/auth/token", json={"provider": "google", "idp_token": idp}
    )
    assert tok.status_code == 200, tok.text
    headers = {"Authorization": f"Bearer {tok.json()['access_token']}"}
    created = await api_client.post(
        "/v1/users",
        headers=headers,
        json={"display_name": "m3ws9", "birth_date": birth_date, "profile": {}},
    )
    assert created.status_code == 201, created.text
    return headers


def _future(hours: float) -> str:
    return (SystemClock().now() + timedelta(hours=hours)).isoformat()


def _vec(*components: float) -> str:
    """768次元ベクトル文字列(先頭要素のみ値・残り0 — groupengine流儀)。"""
    vals = [0.0] * 768
    for i, c in enumerate(components):
        vals[i] = c
    return "[" + ",".join(repr(v) for v in vals) + "]"


E1 = _vec(1.0)  # 全員同一(similarity=1)


def _structured(
    *,
    start: str | None = None,
    expires: str | None = None,
    visibility: str = "summary_only",
    notification_level: str | None = None,
    secondary: str | None = None,
    budget_max: int | None = None,
    participants: tuple[int, int] | None = None,
) -> dict:
    """visibilityは常時明示(groupengine流儀 — DB既定がhidden_until_matchの
    ため省略すると意図しない分岐になる・0001初期スキーマ実査)。"""
    d: dict = {
        "category": {"primary": CATEGORY, "secondary": secondary},
        "alcohol_involved": False,
        "time": {"start": start or _future(BASE_HOURS), "end": None},
        "location": {"name": "天文館"},
        "visibility": visibility,
    }
    if expires is not None:
        d["expires_at"] = expires
    if notification_level is not None:
        d["notification_level"] = notification_level
    if budget_max is not None:
        d["budget"] = {"max": budget_max}
    if participants is not None:
        d["participants"] = {"min": participants[0], "max": participants[1]}
    return d


async def _intent(api_client, db_engine, headers, structured) -> dict:
    """active Intentを作りembeddingを直接挿入(ws流儀のfixture方針)。"""
    resp = await api_client.post(
        "/v1/intents",
        headers=headers,
        json={
            "raw_text": "分類用テキスト",
            "status": "active",
            "structured_intent": structured,
        },
    )
    assert resp.status_code == 201, resp.text
    intent = resp.json()["intent"]
    async with db_engine.begin() as conn:
        await conn.execute(
            text(
                "UPDATE intents SET embedding = CAST(:v AS vector)"
                " WHERE id = CAST(:id AS uuid)"
            ),
            {"v": E1, "id": intent["id"]},
        )
    return intent


async def _seed_jev(db_engine, a_id: str, b_id: str, wa: float, wb: float) -> None:
    """match_candidatesへjev_result付きevaluatedを直接投入。"""
    jev = json.dumps(
        {
            "would_a_accept_b": wa,
            "would_b_accept_a": wb,
            "provider": "fixture",
            "model": None,
        }
    )
    async with db_engine.begin() as conn:
        res = await conn.execute(
            text(
                "UPDATE match_candidates SET jev_result = CAST(:jev AS jsonb),"
                " status = 'evaluated'"
                " WHERE status = 'pending'"
                " AND ((intent_a_id = CAST(:a AS uuid)"
                " AND intent_b_id = CAST(:b AS uuid))"
                " OR (intent_a_id = CAST(:b AS uuid)"
                " AND intent_b_id = CAST(:a AS uuid)))"
            ),
            {"jev": jev, "a": a_id, "b": b_id},
        )
        assert res.rowcount == 1, "jev直接投入対象行なし"


def _latch_engine(db_engine, clock, push=None) -> LatchEngine:
    return LatchEngine(
        engine=db_engine, clock=clock, geo=GeoService(db_engine), push=push
    )


async def _handle(db_engine, clock, intent_id: str, push=None) -> None:
    await _latch_engine(db_engine, clock, push).handle(uuid_mod.UUID(intent_id))


def _push_records(caplog) -> list[dict]:
    return [json.loads(r.getMessage()) for r in caplog.records if r.name == LOGGER_NAME]


def _clock() -> FakeClock:
    return FakeClock(SystemClock().now())


async def _user_id_of(db_engine, intent_id: str) -> str:
    async with db_engine.connect() as conn:
        return str(
            (
                await conn.execute(
                    text("SELECT user_id FROM intents WHERE id = CAST(:id AS uuid)"),
                    {"id": intent_id},
                )
            ).scalar_one()
        )


async def _notifications_rows(db_engine, user_id: str) -> list[tuple]:
    async with db_engine.connect() as conn:
        rows = (
            await conn.execute(
                text(
                    "SELECT id, type, read_at, created_at FROM notifications"
                    " WHERE user_id = CAST(:u AS uuid)"
                    " ORDER BY created_at DESC, id DESC"
                ),
                {"u": user_id},
            )
        ).fetchall()
    return rows


async def _latch_id_of(db_engine, intent_ids: list[str]) -> str:
    """指定Intent群を含むlatches行のid(test_3(notifications)流儀の一般化)。"""
    arr = ", ".join(f"CAST('{i}' AS uuid)" for i in intent_ids)
    async with db_engine.connect() as conn:
        return str(
            (
                await conn.execute(
                    text(
                        f"SELECT id FROM latches"
                        f" WHERE intent_ids && ARRAY[{arr}]::uuid[]"
                    )
                )
            ).scalar_one()
        )


async def _latch(
    db_engine,
    intent_ids: list[str],
    *,
    status: str = "proposed",
    score: float = 0.85,
    deadline_hours: float = 2.0,
    expires_hours: float = 120.0,
    proposal: dict | None = None,
) -> str:
    """latches行を直接INSERT(test_latches_api流儀。回答APIはlatches行のみ読む)。

    now基準はSystemClock(api常設プロセスのClockと同一・回答APIと干渉しない)。
    """
    now = SystemClock().now()
    if proposal is None:
        proposal = {"headcount": len(intent_ids), "match_level": "medium"}
    async with db_engine.begin() as conn:
        res = await conn.execute(
            text("""
                INSERT INTO latches
                    (intent_ids, proposal, score, status,
                     response_deadline, expires_at, created_at)
                VALUES (CAST(:ids AS uuid[]),
                        CAST(:proposal AS jsonb), :score, :status,
                        CAST(:deadline AS timestamptz),
                        CAST(:expires AS timestamptz), CAST(:now AS timestamptz))
                RETURNING id
            """),
            {
                "ids": [uuid_mod.UUID(i) for i in intent_ids],
                "proposal": json.dumps(proposal, ensure_ascii=False),
                "score": score,
                "status": status,
                "deadline": now + timedelta(hours=deadline_hours),
                "expires": now + timedelta(hours=expires_hours),
                "now": now,
            },
        )
    return str(res.first()[0])


async def _respond(api_client, headers, latch_id: str, response: str):
    return await api_client.post(
        f"/v1/latches/{latch_id}/response",
        headers=headers,
        json={"response": response},
    )


async def _latch_row(db_engine, latch_id: str) -> dict:
    async with db_engine.connect() as conn:
        row = (
            (
                await conn.execute(
                    text(
                        "SELECT status, responses FROM latches"
                        " WHERE id = CAST(:l AS uuid)"
                    ),
                    {"l": latch_id},
                )
            )
            .mappings()
            .first()
        )
    assert row is not None
    return dict(row)


async def _events(db_engine, latch_id: str) -> list[tuple]:
    async with db_engine.connect() as conn:
        rows = (
            await conn.execute(
                text(
                    "SELECT from_status, to_status, user_id FROM latch_status_events"
                    " WHERE latch_id = CAST(:l AS uuid) ORDER BY created_at"
                ),
                {"l": latch_id},
            )
        ).fetchall()
    return [(str(r[0]), r[1], None if r[2] is None else str(r[2])) for r in rows]


def _sweeper(db_engine, clock):
    """実LatchEngineをdrain対象に持つExpirySweeper(test_expiry_batches流儀)。"""
    latch_engine = LatchEngine(engine=db_engine, clock=clock)
    return ExpirySweeper(
        engine=db_engine, clock=clock, latch=latch_engine, batch_limit=50
    )


async def _group_of(db_engine, intent_ids: list[str]):
    """指定メンバー(sorted)と一致するgroup_candidates行(開いている行優先)。"""
    arr = ", ".join(f"CAST('{i}' AS uuid)" for i in sorted(intent_ids, key=str))
    async with db_engine.connect() as conn:
        return (
            await conn.execute(
                text(
                    f"SELECT id, status, member_scores, aggregate_score"
                    f" FROM group_candidates"
                    f" WHERE intent_ids = ARRAY[{arr}]::uuid[]"
                    f" ORDER BY (status IN ('candidate','proposed')) DESC,"
                    f" updated_at DESC LIMIT 1"
                )
            )
        ).first()


async def _pair_row(db_engine, a: str, b: str):
    lo, hi = sorted([a, b], key=str)
    async with db_engine.connect() as conn:
        return (
            await conn.execute(
                text(
                    "SELECT id, status, jev_result, latch_score FROM"
                    " match_candidates WHERE intent_a_id = CAST(:a AS uuid)"
                    " AND intent_b_id = CAST(:b AS uuid)"
                    " ORDER BY created_at DESC LIMIT 1"
                ),
                {"a": lo, "b": hi},
            )
        ).first()


def _all_keys(obj) -> set:
    """dictを再帰的に歩いて全キーを集める(値リスト内のdictも。試験2用)。"""
    keys: set = set()
    if isinstance(obj, dict):
        for k, v in obj.items():
            keys.add(k)
            keys |= _all_keys(v)
    elif isinstance(obj, list):
        for item in obj:
            keys |= _all_keys(item)
    return keys


# -- design §2.3 試験1〜6 --


async def test_1_muted_notification_chain_answerable(
    api_client, db_engine, field, caplog
):
    """02#13 muted含むドライラン+回答可能チェーン(G3条件1・design §2.3試験1)。

    A(proposals_only)×B(muted)の閾値超過候補: ①push記録はA側1件のみ
    (mutedのIntentではドライラン記録に提案通知が出ない)②notifications行も
    B側0行③GET /v1/latchesのB視点一覧に当該latchがstatus=proposedで含まれる
    (ホームのLATCH候補)④Bの回答が200で受理されmy_response="yes"(回答可能)。
    既存test_3(notifications)との差分=③④(同試験はDBのstatus確認まで)。
    """
    clock = _clock()
    start = _future(BASE_HOURS)
    expires = _future(FAR_EXPIRES_H)
    ha = await _user(api_client, field)
    a = await _intent(
        api_client,
        db_engine,
        ha,
        _structured(start=start, expires=expires, notification_level="proposals_only"),
    )
    hb = await _user(api_client, field)
    b = await _intent(
        api_client,
        db_engine,
        hb,
        _structured(start=start, expires=expires, notification_level="muted"),
    )
    async with db_engine.begin() as conn:
        await run_candidate_retrieval(conn, clock, uuid_mod.UUID(a["id"]))
    await _seed_jev(db_engine, a["id"], b["id"], 0.9, 0.85)
    with caplog.at_level(logging.INFO, logger=LOGGER_NAME):
        await _handle(db_engine, clock, a["id"], push=StubPushSender(clock=clock))
    recs = _push_records(caplog)
    uid_a = await _user_id_of(db_engine, a["id"])
    uid_b = await _user_id_of(db_engine, b["id"])
    # ①muted側(b)の提案通知はドライラン記録に出ない(完全列挙)
    assert [r["user_id"] for r in recs] == [uid_a]
    for r in recs:
        assert r["notification_type"] == "proposal"
        assert r["status"] == "ok"
    # ②notifications行もb側0行
    assert len(await _notifications_rows(db_engine, uid_b)) == 0
    # ③ホームのLATCH候補: B視点の一覧にstatus=proposedで含まれる
    latch_id = await _latch_id_of(db_engine, [a["id"], b["id"]])
    resp = await api_client.get("/v1/latches", headers=hb)
    assert resp.status_code == 200, resp.text
    items = [i for i in resp.json()["items"] if i["id"] == latch_id]
    assert len(items) == 1
    assert items[0]["status"] == "proposed"
    # ④Bの回答が200で受理(my_response="yes"・A未回答のためpartial_accept)
    r = await _respond(api_client, hb, latch_id, "yes")
    assert r.status_code == 200, r.text
    body = r.json()["latch"]
    assert body["my_response"] == "yes"
    assert body["status"] == "partial_accept"


# GET /v1/latches/{id}の応答キー規定集合(LatchDetailOut全13キー — §10-1と突合済み。
# proposedでは participants/time_summary/area_name は None 値だがキーとして存在)
EXPECTED_DETAIL_KEYS = {
    "id",
    "status",
    "response_deadline",
    "expires_at",
    "created_at",
    "completed_at",
    "proposal",
    "is_group",
    "my_response",
    "remaining_responses",
    "participants",
    "time_summary",
    "area_name",
}
# summary_only双方のproposalキー(proposal.build_proposal実装と突合済み・§10-6)
SUMMARY_PROPOSAL_KEYS = {
    "time_summary",
    "area_name",
    "headcount",
    "category_primary",
    "category_secondary",
    "budget",
    "match_level",
}
# raw_text相当・NG条件文言・表示名・プロフィールに対応するフィールド(不在の証明)
FORBIDDEN_KEYS = {
    "raw_text",
    "soft_constraints",
    "ng_conditions",
    "soft_texts",
    "display_name",
    "profile",
    "user_id",
    "bio",
    "name",
}
# 座標系フィールド(02#23: 座標精度の値が表示されないことの否定的証明)
GEO_KEYS = {
    "lon",
    "lat",
    "geo_lon",
    "geo_lat",
    "geo_center",
    "geo_radius_m",
    "latitude",
    "longitude",
    "coordinates",
    "location",
    "accuracy",
    "radius",
}


async def test_2_visibility_ab_comparison_and_no_coordinates(
    api_client, db_engine, field
):
    """02#22 公開設定2値のA/B比較+02#23 座標非開示(G3条件1・design §2.3試験2)。

    同一条件(時間・地域・カテゴリ・予算)の2組を実際のパイプライン
    (retrieval→jev投入→LatchEngine.handle)でproposed化し、GET /v1/latches/{id}
    を各組の参加者双方の視点で比較する。①組1(summary_only×2)のproposalは
    条件サマリ7キー②組2(hidden混在)は{headcount, match_level}のみ
    (「条件が合う候補があります」相当・一致度=match_level・回答期限=
    response_deadlineはproposal外のトップレベルフィールドで双方に存在)③差分==
    条件サマリ5情報のみ④raw_text相当・NG条件・表示名・プロフィールが応答全体に
    存在しない⑤応答トップレベルのキー集合が規定13キーと完全一致(座標的
    フィールドが構造上存在しないことの否定的証明。位置表現はproposal.area_name
    =逆転ジオコーディングの地名文字列のみ)。
    """
    clock = _clock()
    start = _future(BASE_HOURS)
    expires = _future(FAR_EXPIRES_H)
    cases = [  # (a側visibility, b側visibility) — 以外の条件は両組完全同一
        ("summary_only", "summary_only"),
        ("hidden_until_match", "summary_only"),
    ]
    header_pairs = []
    latch_ids = []
    for va, vb in cases:
        ha = await _user(api_client, field)
        a = await _intent(
            api_client,
            db_engine,
            ha,
            _structured(
                start=start,
                expires=expires,
                visibility=va,
                secondary="焼肉",
                budget_max=5000,
            ),
        )
        hb = await _user(api_client, field)
        b = await _intent(
            api_client,
            db_engine,
            hb,
            _structured(
                start=start,
                expires=expires,
                visibility=vb,
                secondary="焼肉",
                budget_max=5000,
            ),
        )
        async with db_engine.begin() as conn:
            await run_candidate_retrieval(conn, clock, uuid_mod.UUID(a["id"]))
        await _seed_jev(db_engine, a["id"], b["id"], 0.9, 0.9)
        await _handle(db_engine, clock, a["id"])
        header_pairs.append((ha, hb))
        latch_ids.append(await _latch_id_of(db_engine, [a["id"], b["id"]]))
    # 参加者双方の視点で詳細取得(4視点)
    views: list[dict] = []
    for (h1, h2), lid in zip(header_pairs, latch_ids, strict=True):
        for h in (h1, h2):
            resp = await api_client.get(f"/v1/latches/{lid}", headers=h)
            assert resp.status_code == 200, resp.text
            views.append(resp.json()["latch"])
    summary_views = views[:2]  # 組1(a1・b1視点)
    hidden_views = views[2:]  # 組2(a2・b2視点)
    # ①組1(summary_only×2): 条件サマリ7キー+値の検証
    for body in summary_views:
        assert set(body["proposal"]) == SUMMARY_PROPOSAL_KEYS
        p = body["proposal"]
        assert p["headcount"] == 2
        assert p["match_level"] == "high"  # min(0.9, 0.9)=0.90→high
        assert p["budget"] == {"max": 5000}
        assert p["category_primary"] == CATEGORY
        assert p["category_secondary"] == "焼肉"
        datetime.strptime(p["time_summary"], "%Y-%m-%d %H:%M")  # JST書式
        assert isinstance(p["area_name"], str) and p["area_name"]  # 地名(実geo)
    # ②組2(hidden混在): {headcount, match_level}のみ
    for body in hidden_views:
        assert set(body["proposal"]) == {"headcount", "match_level"}
        assert body["proposal"] == {"headcount": 2, "match_level": "high"}
    # ③キー集合の差分==条件サマリ5情報のみ
    assert SUMMARY_PROPOSAL_KEYS - {"headcount", "match_level"} == {
        "time_summary",
        "area_name",
        "category_primary",
        "category_secondary",
        "budget",
    }
    # ④禁止フィールド(raw_text相当・NG条件文言・表示名・プロフィール)不在
    for body in views:
        assert _all_keys(body) & FORBIDDEN_KEYS == set(), body
    # ⑤#23: トップレベル13キー完全一致+座標系キー不在+proposed詳細のNULL 3キー
    for body in views:
        assert set(body) == EXPECTED_DETAIL_KEYS
        assert body["status"] == "proposed"  # パイプラインが提案化した前提の保証
        assert body["participants"] is None  # 成立前の情報最小化
        assert body["time_summary"] is None  # トップレベル(成立後解放用)
        assert body["area_name"] is None  # トップレベル(成立後解放用)
        assert body["is_group"] is False
        assert body["remaining_responses"] == 2
        assert body["my_response"] is None
        assert body["response_deadline"] is not None  # 回答期限は双方に存在
        assert _all_keys(body) & GEO_KEYS == set(), body


async def test_3_group_deadline_expires_with_three_yes(api_client, db_engine, field):
    """09 §3 期限時の確定条件(G3条件2・design §2.3試験3): 4人組で3人YES
    (partial_accept)のままresponse_deadlineを経過→expiredで閉じる
    (3人YESでも成立しない=matchedにならない)。4人目の回答は409。

    deadline過去化はapi常設プロセスのClock(SystemClock)が差し替え不能な
    ためDB値操作(ws-1計画§9-2規律・test_expiry_batches試験3と同一手法)。
    FakeClockをset/advanceしないためrun_once内nowがsweeper構築時の
    last_tickと同一時刻になり(created_at > last_tick不成立)クローズ検知
    drainは観測しない(test_expiry_batches試験2と同型)。
    """
    hs = [await _user(api_client, field) for _ in range(4)]
    intents = [await _intent(api_client, db_engine, h, _structured()) for h in hs]
    ids = [i["id"] for i in intents]
    latch = await _latch(db_engine, ids)  # 4人組・proposed・deadline未来
    # 3人yes → partial_accept・remaining=1(3人YESでも成立しない)
    for h in hs[:3]:
        r = await _respond(api_client, h, latch, "yes")
        assert r.status_code == 200, r.text
    assert (await _latch_row(db_engine, latch))["status"] == "partial_accept"
    # deadlineを過去へ(sweeper遅延でstatus=partial_acceptのままの状態を再現)
    async with db_engine.begin() as conn:
        await conn.execute(
            text(
                "UPDATE latches SET response_deadline ="
                " CAST(:past AS timestamptz) WHERE id = CAST(:l AS uuid)"
            ),
            {"past": SystemClock().now() - timedelta(hours=1), "l": latch},
        )
    clock = FakeClock(SystemClock().now())
    # ①run_onceが当該latchを処理(他試験残骸はteardown+時間窓分離で不在)
    assert await _sweeper(db_engine, clock).run_once() == 1
    # ②expired(matchedにならない)
    assert (await _latch_row(db_engine, latch))["status"] == "expired"
    # ③latch_status_events: partial_accept→expired・user_id=NULL(システム起因)
    assert ("partial_accept", "expired", None) in await _events(db_engine, latch)
    # ④4人目の回答は409
    r4 = await _respond(api_client, hs[3], latch, "yes")
    assert r4.status_code == 409, r4.text
    assert r4.json()["error"]["code"] == "LATCH_EXPIRED"


async def test_4_group_match_closes_one_on_one(api_client, db_engine, field):
    """09 §3 競合クローズ・グループ成立側(G3条件2・design §2.3試験4)。

    i2を共有する2つの提案(グループ{i1,i2,i3}と1対1{i2,iX})を用意し、
    グループを成立させる→1対1側はcancelledで閉じる(対象抽出は
    intent_ids && member_ids=同一Intent含有・§10-2の_SELECT_CONFLICTING_
    LATCHES)。iX(非共有)はactiveのまま。test_11(latches・1対1×1対1)の
    グループ成立側対。
    """
    h1 = await _user(api_client, field)
    h2 = await _user(api_client, field)
    h3 = await _user(api_client, field)
    hx = await _user(api_client, field)  # iXの所有者(外部ユーザーU4相当)
    i1 = await _intent(api_client, db_engine, h1, _structured())
    i2 = await _intent(api_client, db_engine, h2, _structured())
    i3 = await _intent(api_client, db_engine, h3, _structured())
    ix = await _intent(api_client, db_engine, hx, _structured())
    group_latch = await _latch(db_engine, [i1["id"], i2["id"], i3["id"]])
    pair_latch = await _latch(db_engine, [i2["id"], ix["id"]])
    # グループ3人yes → matched
    for h in (h1, h2, h3):
        r = await _respond(api_client, h, group_latch, "yes")
        assert r.status_code == 200, r.text
    # ①グループmatched・参加Intentはmatched(05 §6 Intent遷移)
    assert (await _latch_row(db_engine, group_latch))["status"] == "matched"
    # ②1対1側はcancelled(もう片方はcancelledで閉じる)
    assert (await _latch_row(db_engine, pair_latch))["status"] == "cancelled"
    # ③latch_status_events(1対1側: proposed→cancelled・user_id=NULL=システム起因)
    assert ("proposed", "cancelled", None) in await _events(db_engine, pair_latch)
    # ④Intent遷移: グループ側3つはmatched・iXはactiveのまま
    async with db_engine.connect() as conn:
        rows = (
            await conn.execute(
                text(
                    "SELECT id, status FROM intents"
                    " WHERE id = ANY(CAST(:ids AS uuid[]))"
                ),
                {
                    "ids": [
                        uuid_mod.UUID(x)
                        for x in (i1["id"], i2["id"], i3["id"], ix["id"])
                    ]
                },
            )
        ).fetchall()
    by_id = {str(r[0]): r[1] for r in rows}
    assert by_id[i1["id"]] == "matched"
    assert by_id[i2["id"]] == "matched"
    assert by_id[i3["id"]] == "matched"
    assert by_id[ix["id"]] == "active"  # 閉じるのはi2を含む提案のみ


async def test_5_group_defer_rejects(api_client, db_engine, field):
    """09 §3 部分成立の禁止・defer側(G3条件2・design §2.3試験5)。

    3人グループで2人yes(partial_accept)→1人defer→即rejected(部分成立なし)。
    「一部がnoまたはdefer」のdefer側(no側は既存test_9(latches)担当)。
    """
    hs = [await _user(api_client, field) for _ in range(3)]
    intents = [await _intent(api_client, db_engine, h, _structured()) for h in hs]
    latch = await _latch(db_engine, [i["id"] for i in intents])
    await _respond(api_client, hs[0], latch, "yes")
    r2 = await _respond(api_client, hs[1], latch, "yes")
    assert r2.status_code == 200, r2.text
    assert r2.json()["latch"]["status"] == "partial_accept"
    r3 = await _respond(api_client, hs[2], latch, "defer")
    assert r3.status_code == 200, r3.text
    assert r3.json()["latch"]["status"] == "rejected"  # 即時・部分成立なし
    row = await _latch_row(db_engine, latch)
    assert row["status"] == "rejected"
    assert row["responses"][-1]["response"] == "defer"  # 区別保持


async def test_6_self_duplicate_group_excluded(api_client, db_engine, field):
    """FR-17(10 §3)自己重複集合の除外・統合配置ケース(G3条件3・design §2.3試験6)。

    同一ユーザーUのIntent 2つ(s・s2)+別ユーザーV・WのIntent各1を同一条件
    (時間Bucket・地域・カテゴリ)で配置しGroupEngine.handle(s)を実行:
    ①{s,s2}を同時に含くgroup_candidates行==0件(同一ユーザーのIntentを2つ
    含む集合が生成されない)②対照: {s,V,W}は生成される(検証環境が通過状態
    であることの保証)③s×s2のペア行(match_candidates)なし。多層防御=
    Pool検索のLAYER1_WHERE_BASE(i.user_id <> :origin_user_id・§10-7)が
    前段でs2をPoolから除外し、_PAIR_COMPAT(i1.user_id <> i2.user_id)・
    貪欲法のuser相異(group_calc.build_groups・§10-8)・INSERT前user相異検査
    が続く。1対1側(hardfilter test_10)と対のグループ経路確認。
    """
    clock = _clock()
    start = _future(BASE_HOURS)
    expires = _future(FAR_EXPIRES_H)
    hu = await _user(api_client, field)  # ユーザーU(2 Intent)
    s = await _intent(
        api_client,
        db_engine,
        hu,
        _structured(start=start, expires=expires, participants=(2, 4)),
    )
    s2 = await _intent(
        api_client,
        db_engine,
        hu,
        _structured(start=start, expires=expires, participants=(2, 4)),
    )
    hv = await _user(api_client, field)
    v = await _intent(
        api_client,
        db_engine,
        hv,
        _structured(start=start, expires=expires, participants=(2, 4)),
    )
    hw = await _user(api_client, field)
    w = await _intent(
        api_client,
        db_engine,
        hw,
        _structured(start=start, expires=expires, participants=(2, 4)),
    )
    group = GroupEngine(engine=db_engine, clock=clock)
    ctx = await group.handle(uuid_mod.UUID(s["id"]))
    assert ctx is not None  # handle自体は集合を生成する(対照の前提)
    # ①{s,s2}を同時に含む集合行==0件
    async with db_engine.connect() as conn:
        n_bad = (
            await conn.execute(
                text(
                    "SELECT COUNT(*) FROM group_candidates"
                    " WHERE intent_ids @>"
                    " ARRAY[CAST(:s AS uuid), CAST(:s2 AS uuid)]"
                ),
                {"s": s["id"], "s2": s2["id"]},
            )
        ).scalar_one()
    assert n_bad == 0
    # ②対照: {s,V,W}(Uの2つ目を含まない3人集合)は生成される
    # (全員の[min,max]に3が∈のため成立・偽陰性の対抗)
    gc = await _group_of(db_engine, [s["id"], v["id"], w["id"]])
    assert gc is not None and gc[1] == "candidate"
    # ③s×s2のペア行なし(Pool×起点UPSERTの対象外=自己除外の実効)
    assert await _pair_row(db_engine, s["id"], s2["id"]) is None
