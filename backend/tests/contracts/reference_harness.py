"""参照実装(PostgreSQL・compose常設api)用harness。

契約ケースの実行に必要な「実装固有の事前状態」をここで用意する。

- actor provisioning(認証→登録→同意)
- Clock由来テンプレート変数({future_2h}等)
- 名前付きseed(latches行・calibration行・notifications行の直接INSERT)
- teardown(FK順の全削除・subjectプレフィックス単位)

移行実装(D1)側は同じinterfaceで別実装を提供する(test_api_parity.pyは
ケースとこのharnessだけを見る)。
"""

from __future__ import annotations

import json
import uuid as uuid_mod
from datetime import timedelta
from typing import Any

import jwt as pyjwt
from sqlalchemy import text

from latch.auth.tools import issue_idp_token
from latch.core.clock import SystemClock

SUBJECT_PREFIX = "t0c-"

INTENT_BODY = {
    "raw_text": "seed用の意図テキスト",
    "status": "active",
    "structured_intent": {
        "category": {"primary": "meal", "secondary": None},
        "alcohol_involved": False,
        "time": {"start": None, "end": None},  # future_2h を実行時に埋める
        "location": {"name": "天文館"},
    },
}

# actor名 → provisioning設定
ACTOR_SPEC: dict[str, dict[str, Any]] = {
    "fresh": {"register": False},
    "u1": {"register": True, "consent": True},
    "u2": {"register": True, "consent": True},
    "u3": {"register": True, "consent": True},
    "u4": {"register": True, "consent": True},
    "outsider": {"register": True, "consent": True},
    "ghost": {"register": False},
    "minor19": {"register": True, "consent": True, "birth_date": "minor19"},
    "u1_noconsent": {"register": True, "consent": False},
    "u1_del": {"register": True, "consent": True},
    "u1_logged_out": {"register": True, "consent": True, "logout": True},
    # survey(V1-P3・05 v0.7)。u1〜u4は他領域のseedでIntentを作るため
    # due判定の状態を固定する専用actorを分ける
    "sv1": {"register": True, "consent": True},  # Intentなし(not_due固定)
    "sv2": {"register": True, "consent": True},  # onboarding往復チェーン用
    "sv3": {"register": True, "consent": True},  # week4(seedで29日前回答)
    # ab(V1-P4・05 v0.8)。割付の観測API用(actor単位でseedを分ける)
    "ab1": {"register": True, "consent": True},  # 割付なし(空配列固定)
    "ab2": {"register": True, "consent": True},  # seed=latch_threshold/treatment
    "ab3": {"register": True, "consent": True},  # seed=notification_daily_limit/control
    # push(V1-P5・05 v0.9)。トークン登録API用(setupのHTTP POSTで状態を作る)
    "pu1": {"register": True, "consent": True},  # 登録・一覧の主
    "pu2": {"register": True, "consent": True},  # 所有権移動の受け手
}


class ReferenceHarness:
    """api_client(compose api)とdb_engineを使う契約実行harness。"""

    def __init__(self, api_client, db_engine) -> None:
        self._api = api_client
        self._engine = db_engine
        self._prefix = f"{SUBJECT_PREFIX}{uuid_mod.uuid4().hex[:8]}-"
        self._tokens: dict[str, str] = {}
        self._subjects: dict[str, str] = {}
        self._user_ids: dict[str, str] = {}

    # -- actor / 変数 ---------------------------------------------------------

    @property
    def prefix(self) -> str:
        return self._prefix

    async def resolve_token(self, actor: str) -> str:
        """actorのaccess JWTを(必要ならprovisionして)返す。"""
        if actor in self._tokens:
            return self._tokens[actor]
        spec = ACTOR_SPEC.get(actor)
        if spec is None:
            raise KeyError(f"未知のactor: {actor!r}")
        if actor not in self._subjects:
            self._subjects[actor] = f"{self._prefix}{actor}-{uuid_mod.uuid4().hex[:8]}"
        subject = self._subjects[actor]
        idp = issue_idp_token(provider="google", subject=subject)
        resp = await self._api.post(
            "/v1/auth/token", json={"provider": "google", "idp_token": idp}
        )
        assert resp.status_code == 200, resp.text
        body = resp.json()
        access = body["access_token"]
        headers = {"Authorization": f"Bearer {access}"}
        if spec.get("register"):
            birth_date = self._birth_date(spec.get("birth_date"))
            resp = await self._api.post(
                "/v1/users",
                headers=headers,
                json={
                    "display_name": f"契約-{actor}",
                    "birth_date": birth_date,
                    "profile": {},
                },
            )
            assert resp.status_code == 201, resp.text
            self._user_ids[actor] = resp.json()["user"]["id"]
        if spec.get("consent"):
            resp = await self._api.post(
                "/v1/users/me/consent", json={}, headers=headers
            )
            assert resp.status_code == 200, resp.text
        if spec.get("logout"):
            resp = await self._api.post("/v1/auth/logout", json={}, headers=headers)
            assert resp.status_code == 204, resp.text
        self._tokens[actor] = access
        return access

    async def prepare_case_vars(self, case) -> dict[str, str]:
        """caseに現れるテンプレート変数を解決済みdictで返す。

        user_id変数({u1_id}等)はactor provisioning後に確定するため、
        必要actorを先にprovisionしてから解決する。
        """
        from contracts.runner import collect_var_names

        names = collect_var_names(case)
        # user_id参照があるactorを先にprovision
        for actor in ("u1", "u2", "u3"):
            if any(n == f"{actor}_id" for n in names):
                await self.resolve_token(actor)
        resolved: dict[str, str] = {}
        for name in names:
            if name.startswith("idp_token"):
                resolved[name] = await self.idp_token_var(name)
            elif name.endswith("_id") and name[:-3] in self._user_ids:
                resolved[name] = self._user_ids[name[:-3]]
        return resolved

    @staticmethod
    def _birth_date(kind: str | None) -> str:
        now = SystemClock().now()
        if kind == "minor19":
            return (now - timedelta(days=19 * 365 + 5)).date().isoformat()
        return "1995-06-15"

    def clock_vars(self) -> dict[str, str]:
        now = SystemClock().now()
        access = None
        # tampered_token は u1 のtokenを基に作る(未provisionなら後回し)
        if "u1" in self._tokens:
            token = self._tokens["u1"]
            header, payload, sig = token.split(".")
            flipped = ("A" if sig[0] != "A" else "B") + sig[1:]
            access = f"{header}.{payload}.{flipped}"
        return {
            "uuid4": str(uuid_mod.uuid4()),
            "now_iso": now.isoformat(),
            "future_2h": (now + timedelta(hours=2)).isoformat(),
            "future_8d": (now + timedelta(days=8)).isoformat(),
            "past_1h": (now - timedelta(hours=1)).isoformat(),
            "minor_birth_date": (now - timedelta(days=17 * 365 + 190))
            .date()
            .isoformat(),
            "text_300": "あ" * 300,
            "text_301": "あ" * 301,
            "stub_time_start": "2026-09-27T19:00:00+09:00",
            "tampered_token": access or "invalid.token.value",
        }

    def _idp_token_for_subject(self, subject: str) -> str:
        return issue_idp_token(provider="google", subject=subject)

    async def idp_token_var(self, name: str) -> str:
        """{idp_token:google} / {idp_token:u1_del} / {idp_token_bad}。"""
        if name == "idp_token_bad":
            # 未知kidのJWT → 「signing key not found for kid」(401経路)
            return pyjwt.encode(
                {"sub": "bad-key", "exp": 9999999999},
                key="irrelevant",
                algorithm="HS256",
                headers={"kid": "unknown-kid-xyz"},
            )
        if name.startswith("idp_token:"):
            target = name.split(":", 1)[1]
            if target in ACTOR_SPEC:
                # actorのsubjectで発行(provision前でもsubjectを確定)
                if target not in self._subjects:
                    self._subjects[target] = (
                        f"{self._prefix}{target}-{uuid_mod.uuid4().hex[:8]}"
                    )
                subject = self._subjects[target]
            else:
                subject = f"{self._prefix}anon-{uuid_mod.uuid4().hex[:8]}"
            return self._idp_token_for_subject(subject)
        raise KeyError(f"未知の特殊変数: {name}")

    # -- seeds ----------------------------------------------------------------

    async def _create_intent(self, actor: str) -> str:
        body = json.loads(json.dumps(INTENT_BODY))
        body["structured_intent"]["time"]["start"] = (
            SystemClock().now() + timedelta(hours=2)
        ).isoformat()
        resp = await self._api.post(
            "/v1/intents",
            headers={"Authorization": f"Bearer {await self.resolve_token(actor)}"},
            json=body,
        )
        assert resp.status_code == 201, resp.text
        return resp.json()["intent"]["id"]

    async def seed(self, name: str, params: dict[str, Any]) -> dict[str, Any]:
        if name == "pair_latch":
            return await self._seed_pair_latch(params)
        if name == "group_latch":
            return await self._seed_group_latch(params)
        if name == "completed_latch":
            return await self._seed_completed_latch(params)
        if name == "notification":
            return await self._seed_notification(params)
        if name == "survey_onboarding_answered":
            return await self._seed_survey_onboarding(params)
        if name == "ab_assignment":
            return await self._seed_ab_assignment(params)
        raise KeyError(f"未知のseed: {name}")

    async def _seed_survey_onboarding(self, params: dict[str, Any]) -> dict[str, Any]:
        """sv3のonboarding回答をdays_ago日前へ直接INSERT(week4 dueの駆動)。"""
        days_ago = float(params.get("days_ago", 29))
        await self.resolve_token("sv3")
        now = SystemClock().now()
        async with self._engine.begin() as conn:
            res = await conn.execute(
                text("""
                    INSERT INTO survey_responses
                        (user_id, round, answers, free_comment, created_at)
                    VALUES (CAST(:u AS uuid), 'onboarding',
                            CAST(:answers AS jsonb), NULL,
                            CAST(:at AS timestamptz))
                    RETURNING id
                """),
                {
                    "u": uuid_mod.UUID(self._user_ids["sv3"]),
                    "answers": json.dumps({"convenience": 4}, ensure_ascii=False),
                    "at": now - timedelta(days=days_ago),
                },
            )
            return {"survey_id": str(res.first()[0])}

    async def _insert_latch(
        self,
        intent_ids: list[str],
        *,
        status: str,
        deadline_hours: float,
        expires_hours: float = 120.0,
    ) -> str:
        now = SystemClock().now()
        proposal = {"headcount": len(intent_ids), "match_level": "medium"}
        async with self._engine.begin() as conn:
            res = await conn.execute(
                text("""
                    INSERT INTO latches
                        (intent_ids, group_candidate_id, proposal, score, status,
                         response_deadline, expires_at, created_at)
                    VALUES (CAST(:ids AS uuid[]), NULL,
                            CAST(:proposal AS jsonb), :score, :status,
                            CAST(:deadline AS timestamptz),
                            CAST(:expires AS timestamptz), CAST(:now AS timestamptz))
                    RETURNING id
                """),
                {
                    "ids": [uuid_mod.UUID(i) for i in intent_ids],
                    "proposal": json.dumps(proposal, ensure_ascii=False),
                    "score": 0.85,
                    "status": status,
                    "deadline": now + timedelta(hours=deadline_hours),
                    "expires": now + timedelta(hours=expires_hours),
                    "now": now,
                },
            )
        return str(res.first()[0])

    async def _seed_ab_assignment(self, params: dict[str, Any]) -> dict[str, Any]:
        """actor(ab2/ab3)へ割付行を直接INSERT(実験行は冪等)。"""
        actor = params.get("actor", "ab2")
        experiment = params.get("experiment", "latch_threshold")
        variant = params.get("variant", "treatment")
        await self.resolve_token(actor)
        async with self._engine.begin() as conn:
            await conn.execute(
                text(
                    "INSERT INTO ab_experiments (experiment_key, status,"
                    " control_value, treatment_value, started_at)"
                    " VALUES (:k, 'running', :c, :t, now())"
                    " ON CONFLICT (experiment_key) DO NOTHING"
                ),
                {"k": experiment, "c": "0.6", "t": "0.5"},
            )
            await conn.execute(
                text(
                    "INSERT INTO ab_assignments (experiment_key, user_id,"
                    " variant, stratum, assigned_at) VALUES (:k,"
                    " CAST(:u AS uuid), :v, '0', now())"
                ),
                {
                    "k": experiment,
                    "u": self._user_ids[actor],
                    "v": variant,
                },
            )
        return {"experiment": experiment, "variant": variant}

    async def _seed_pair_latch(self, params: dict[str, Any]) -> dict[str, Any]:
        a = await self._create_intent("u1")
        b = await self._create_intent("u2")
        latch_id = await self._insert_latch(
            [a, b],
            status=params.get("status", "proposed"),
            deadline_hours=params.get("deadline_hours", 2.0),
        )
        return {"latch_id": latch_id, "intent_ids": [a, b]}

    async def _seed_group_latch(self, params: dict[str, Any]) -> dict[str, Any]:
        size = int(params.get("size", 3))
        actors = ["u1", "u2", "u3", "u4"][:size]
        intent_ids = [await self._create_intent(actor) for actor in actors]
        latch_id = await self._insert_latch(
            intent_ids, status="proposed", deadline_hours=2.0
        )
        return {"latch_id": latch_id, "intent_ids": intent_ids}

    async def _seed_completed_latch(self, params: dict[str, Any]) -> dict[str, Any]:
        hours_ago = float(params.get("hours_ago", 1.0))
        seeded = await self._seed_pair_latch({"status": "matched"})
        latch_id = seeded["latch_id"]
        now = SystemClock().now()
        async with self._engine.begin() as conn:
            await conn.execute(
                text(
                    "UPDATE latches SET status = 'completed',"
                    " completed_at = CAST(:c AS timestamptz)"
                    " WHERE id = CAST(:l AS uuid)"
                ),
                {"c": now - timedelta(hours=hours_ago), "l": latch_id},
            )
            await conn.execute(
                text("""
                    INSERT INTO calibration_records
                        (latch_id, intent_ids, prediction, proposal_snapshot,
                         actual_responses, matched, created_at, updated_at)
                    VALUES (CAST(:l AS uuid), CAST(:ids AS uuid[]),
                            CAST(:pred AS jsonb), CAST(:prop AS jsonb),
                            CAST(:resp AS jsonb), true,
                            CAST(:now AS timestamptz), CAST(:now AS timestamptz))
                """),
                {
                    "l": latch_id,
                    "ids": [uuid_mod.UUID(i) for i in seeded["intent_ids"]],
                    "pred": json.dumps({"placeholder": True}),
                    "prop": json.dumps({"headcount": 2}),
                    "resp": json.dumps([]),
                    "now": now - timedelta(hours=1),
                },
            )
        return seeded

    async def _seed_notification(self, params: dict[str, Any]) -> dict[str, Any]:
        seeded = await self._seed_pair_latch({"status": "proposed"})
        await self.resolve_token("u1")
        now = SystemClock().now()
        async with self._engine.begin() as conn:
            res = await conn.execute(
                text("""
                    INSERT INTO notifications
                        (user_id, type, payload, created_at)
                    VALUES (CAST(:u AS uuid), 'proposal',
                            CAST(:p AS jsonb), CAST(:now AS timestamptz))
                    RETURNING id
                """),
                {
                    "u": uuid_mod.UUID(self._user_ids["u1"]),
                    "p": json.dumps(
                        {"latch_id": seeded["latch_id"]}, ensure_ascii=False
                    ),
                    "now": now,
                },
            )
            seeded["notification_id"] = str(res.first()[0])
        return seeded

    # -- teardown --------------------------------------------------------------

    async def cleanup(self) -> None:
        p = {"p": self._prefix + "%"}
        async with self._engine.begin() as conn:
            await conn.execute(
                text(
                    "DELETE FROM push_tokens WHERE user_id IN"
                    " (SELECT id FROM users WHERE auth_subject LIKE :p)"
                ),
                p,
            )
            await conn.execute(
                text(
                    "DELETE FROM ab_assignments WHERE user_id IN"
                    " (SELECT id FROM users WHERE auth_subject LIKE :p)"
                ),
                p,
            )
            await conn.execute(
                text(
                    "DELETE FROM ab_experiments WHERE experiment_key IN"
                    " ('latch_threshold', 'notification_daily_limit',"
                    " 'notification_concurrent_limit')"
                ),
            )
            await conn.execute(
                text(
                    "DELETE FROM survey_responses WHERE user_id IN"
                    " (SELECT id FROM users WHERE auth_subject LIKE :p)"
                ),
                p,
            )
            await conn.execute(
                text(
                    "DELETE FROM messages WHERE latch_id IN"
                    " (SELECT id FROM latches WHERE intent_ids && ARRAY("
                    "  SELECT id FROM intents WHERE user_id IN"
                    "  (SELECT id FROM users WHERE auth_subject LIKE :p)))"
                ),
                p,
            )
            await conn.execute(
                text(
                    "DELETE FROM latch_status_events WHERE latch_id IN"
                    " (SELECT id FROM latches WHERE intent_ids && ARRAY("
                    "  SELECT id FROM intents WHERE user_id IN"
                    "  (SELECT id FROM users WHERE auth_subject LIKE :p)))"
                ),
                p,
            )
            await conn.execute(
                text(
                    "DELETE FROM calibration_records WHERE latch_id IN"
                    " (SELECT id FROM latches WHERE intent_ids && ARRAY("
                    "  SELECT id FROM intents WHERE user_id IN"
                    "  (SELECT id FROM users WHERE auth_subject LIKE :p)))"
                ),
                p,
            )
            await conn.execute(
                text(
                    "DELETE FROM notifications WHERE user_id IN"
                    " (SELECT id FROM users WHERE auth_subject LIKE :p)"
                ),
                p,
            )
            await conn.execute(
                text(
                    "DELETE FROM reports WHERE reporter_id IN"
                    " (SELECT id FROM users WHERE auth_subject LIKE :p)"
                    " OR reportee_id IN"
                    " (SELECT id FROM users WHERE auth_subject LIKE :p)"
                ),
                p,
            )
            await conn.execute(
                text(
                    "DELETE FROM latches WHERE intent_ids && ARRAY("
                    "  SELECT id FROM intents WHERE user_id IN"
                    "  (SELECT id FROM users WHERE auth_subject LIKE :p))"
                ),
                p,
            )
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
                    "DELETE FROM group_candidates WHERE intent_ids && ARRAY("
                    "  SELECT id FROM intents WHERE user_id IN"
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
                    "DELETE FROM blocks WHERE blocker_id IN"
                    " (SELECT id FROM users WHERE auth_subject LIKE :p)"
                    " OR blocked_id IN"
                    " (SELECT id FROM users WHERE auth_subject LIKE :p)"
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
            await conn.execute(text("DELETE FROM users WHERE auth_subject LIKE :p"), p)
