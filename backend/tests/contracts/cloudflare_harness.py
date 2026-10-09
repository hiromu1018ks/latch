"""Cloudflare(D1/Workers)実装用harness(本体実装・2026-10-09)。

reference_harness.py と同じinterface(actor provisioning・clock変数・
名前付きseed・cleanup)を2つのモードで提供する:

- local: in-process ASGI(latch_worker.app + LocalD1Database)。
  契約119ケースをminiflare/deployなしで流せる高速検証。
- remote: 実環境URL(https://latch-api.hiromu1018ks.workers.dev)へHTTP。
  seed/cleanupは wrangler d1 execute --remote subprocess経由
  (実APIに提案投入endpointは無いため — referenceがdb_engine直列挿入を
  するのと同じ位置づけ)。

test_cloudflare_parity.py はケースとこのharnessだけを見る
(reference側test_api_parity.pyと対称)。
"""

from __future__ import annotations

import json
import os
import subprocess
import uuid as uuid_mod
from datetime import timedelta
from pathlib import Path
from typing import Any

import jwt as pyjwt
from latch.auth.tools import issue_idp_token
from latch.core.clock import SystemClock
from latch.domain.time import micros

from contracts.reference_harness import ACTOR_SPEC, INTENT_BODY, SUBJECT_PREFIX

REPO = Path(__file__).resolve().parents[3]
WRANGLER_DIR = REPO / "cloudflare" / "api"


def _canonical(value: Any) -> str:
    return json.dumps(value, ensure_ascii=True, separators=(",", ":"))


class WranglerD1:
    """wrangler d1 execute のsubprocess薄wrap(remote/local両モード)。

    .envのCLOUDFLARE_API_TOKENを引き継ぐ。失敗は例外(stderr付き)。
    local=True は miniflare(cf-dev)のD1(pywrangler devと同じstate)。
    """

    def __init__(self, *, database: str = "latch-core", local: bool = False) -> None:
        self._database = database
        self._local = local
        # miniflare(cf-dev)検証用: wrangler --local がdev serverと別sqliteを
        # 参照するため、devのD1 sqliteファイルを直接指定できるようにする
        self._sqlite_path = os.environ.get("LATCH_CF_D1_SQLITE")

    def exec(self, sql: str) -> str:
        if self._sqlite_path:
            import sqlite3

            conn = sqlite3.connect(self._sqlite_path)
            try:
                conn.execute("PRAGMA foreign_keys = ON")
                conn.executescript(sql)
                conn.commit()
            finally:
                conn.close()
            return ""
        env = dict(os.environ)
        env.setdefault(
            "XDG_CONFIG_HOME", str(REPO / ".xdg-config")
        )
        env.setdefault("UV_CACHE_DIR", str(REPO / ".uv-cache"))
        env.setdefault("UV_PYTHON_INSTALL_DIR", str(REPO / ".uv-python"))
        env.setdefault("npm_config_cache", str(REPO / ".npm-cache"))
        args = [
            "npx",
            "wrangler",
            "d1",
            "execute",
            self._database,
        ]
        if self._local:
            args.append("--local")
        args += ["--json", "--command", sql]
        proc = subprocess.run(  # noqa: S603
            args,
            cwd=WRANGLER_DIR,
            env=env,
            capture_output=True,
            text=True,
            timeout=120,
            check=False,
        )
        if proc.returncode != 0:
            raise RuntimeError(
                f"wrangler d1 execute failed: {proc.stderr[-2000:]}"
            )
        return proc.stdout

    def apply_file(self, path: Path) -> str:
        env = dict(os.environ)
        env.setdefault("XDG_CONFIG_HOME", str(REPO / ".xdg-config"))
        if self._sqlite_path:
            return self.exec(path.read_text(encoding="utf-8"))
        args = [
            "npx",
            "wrangler",
            "d1",
            "execute",
            self._database,
        ]
        if self._local:
            args.append("--local")
        args += ["--json", "--file", str(path)]
        proc = subprocess.run(  # noqa: S603
            args,
            cwd=WRANGLER_DIR,
            env=env,
            capture_output=True,
            text=True,
            timeout=300,
            check=False,
        )
        if proc.returncode != 0:
            raise RuntimeError(f"wrangler d1 execute --file failed: {proc.stderr[-2000:]}")
        return proc.stdout


class CloudflareHarness:
    """契約ケース実行harness(local/remote共通のactor・seed・cleanup)。"""

    def __init__(self, api_client, db=None, wrangler: WranglerD1 | None = None) -> None:
        self._api = api_client
        self._db = db  # local: LocalD1Database / remote: None
        self._wr = wrangler  # remote: WranglerD1
        self._prefix = f"{SUBJECT_PREFIX}cf-{uuid_mod.uuid4().hex[:8]}-"
        self._tokens: dict[str, str] = {}
        self._subjects: dict[str, str] = {}
        self._user_ids: dict[str, str] = {}

    @property
    def prefix(self) -> str:
        return self._prefix

    # -- D1実行(local直列/remote wrangler) --

    async def _d1_batch(self, statements: list[str]) -> None:
        if self._db is not None:
            await self._db.batch(
                [self._db.prepare(sql) for sql in statements]
            )
        else:
            assert self._wr is not None
            self._wr.exec("; ".join(statements))

    async def _d1_insert_returning(self, statements: list[str]) -> None:
        await self._d1_batch(statements)

    # -- actor / 変数(reference_harnessと同一) ------------------------------

    async def resolve_token(self, actor: str) -> str:
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
        from contracts.runner import collect_var_names

        names = collect_var_names(case)
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
        if name == "idp_token_bad":
            return pyjwt.encode(
                {"sub": "bad-key", "exp": 9999999999},
                key="irrelevant",
                algorithm="HS256",
                headers={"kid": "unknown-kid-xyz"},
            )
        if name.startswith("idp_token:"):
            target = name.split(":", 1)[1]
            if target in ACTOR_SPEC:
                if target not in self._subjects:
                    self._subjects[target] = (
                        f"{self._prefix}{target}-{uuid_mod.uuid4().hex[:8]}"
                    )
                subject = self._subjects[target]
            else:
                subject = f"{self._prefix}anon-{uuid_mod.uuid4().hex[:8]}"
            return self._idp_token_for_subject(subject)
        raise KeyError(f"未知の特殊変数: {name}")

    # -- seeds(D1 schema: TEXT uuid・INTEGER micros・JSON TEXT) ----------------

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
        raise KeyError(f"未知のseed: {name}")

    async def _insert_latch(
        self,
        intent_ids: list[str],
        *,
        status: str,
        deadline_hours: float,
        expires_hours: float = 120.0,
    ) -> str:
        now = SystemClock().now()
        latch_id = str(uuid_mod.uuid4())
        proposal = {"headcount": len(intent_ids), "match_level": "medium"}
        await self._d1_batch(
            [
                "INSERT INTO latches (id, intent_ids, group_candidate_id, proposal,"
                " score, status, responses, response_deadline, expires_at,"
                " created_at) VALUES ("
                f"'{latch_id}', '{_canonical(intent_ids)}', NULL,"
                f"'{_canonical(proposal)}', 0.85, '{status}', '[]',"
                f"{micros(now + timedelta(hours=deadline_hours))},"
                f"{micros(now + timedelta(hours=expires_hours))},"
                f"{micros(now)})"
            ]
        )
        return latch_id

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
        completed_us = micros(now - timedelta(hours=hours_ago))
        await self._d1_batch(
            [
                f"UPDATE latches SET status = 'completed', completed_at ="
                f" {completed_us} WHERE id = '{latch_id}'",
                "INSERT INTO calibration_records (id, latch_id, intent_ids,"
                " prediction, proposal_snapshot, actual_responses, matched,"
                " created_at, updated_at) VALUES ("
                f"'{uuid_mod.uuid4()}', '{latch_id}',"
                f"'{_canonical(seeded['intent_ids'])}',"
                f"'{_canonical({'placeholder': True})}',"
                f"'{_canonical({'headcount': 2})}', '[]', 1,"
                f"{micros(now - timedelta(hours=1))},"
                f"{micros(now - timedelta(hours=1))})",
            ]
        )
        return seeded

    async def _seed_notification(self, params: dict[str, Any]) -> dict[str, Any]:
        seeded = await self._seed_pair_latch({"status": "proposed"})
        await self.resolve_token("u1")
        now = SystemClock().now()
        notification_id = str(uuid_mod.uuid4())
        await self._d1_batch(
            [
                "INSERT INTO notifications (id, user_id, type, payload, created_at)"
                f" VALUES ('{notification_id}', '{self._user_ids['u1']}',"
                f"'proposal', '{_canonical({'latch_id': seeded['latch_id']})}',"
                f"{micros(now)})"
            ]
        )
        seeded["notification_id"] = notification_id
        return seeded

    # -- teardown(subject prefix単位・FK順) ---------------------------------

    async def cleanup(self) -> None:
        p = self._prefix
        u = "(SELECT id FROM users WHERE auth_subject LIKE '" + p + "%')"
        intents = "(SELECT id FROM intents WHERE user_id IN " + u + ")"
        latches = (
            "(SELECT id FROM latches WHERE EXISTS (SELECT 1 FROM json_each("
            "latches.intent_ids) je WHERE je.value IN " + intents + "))"
        )
        await self._d1_batch(
            [
                # FK参照元から先に消く(journal→commands・ownership→intents/latches)
                "DELETE FROM journal WHERE command_id IN (SELECT id FROM commands"
                " WHERE target_id IN " + intents + " OR target_id IN " + u + ")",
                "DELETE FROM commands WHERE target_id IN " + intents
                + " OR target_id IN " + u,
                f"DELETE FROM messages WHERE latch_id IN {latches}",
                f"DELETE FROM latch_status_events WHERE latch_id IN {latches}",
                f"DELETE FROM calibration_records WHERE latch_id IN {latches}",
                f"DELETE FROM notifications WHERE user_id IN {u}",
                "DELETE FROM reports WHERE reporter_id IN " + u
                + " OR reportee_id IN " + u,
                f"DELETE FROM intent_match_ownership WHERE intent_id IN {intents}",
                f"DELETE FROM latches WHERE id IN {latches}",
                "DELETE FROM match_candidates WHERE intent_a_id IN " + intents
                + " OR intent_b_id IN " + intents,
                "DELETE FROM group_candidates WHERE EXISTS (SELECT 1 FROM json_each("
                "group_candidates.intent_ids) je WHERE je.value IN " + intents + ")",
                f"DELETE FROM match_events WHERE source_intent_id IN {intents}",
                "DELETE FROM blocks WHERE blocker_id IN " + u + " OR blocked_id IN " + u,
                f"DELETE FROM deletion_tombstones WHERE target_id IN {intents}"
                f" OR target_id IN {u}",
                "DELETE FROM refresh_tokens WHERE sid IN (SELECT sid FROM"
                " session_families WHERE subject LIKE '" + p + "%')",
                "DELETE FROM session_families WHERE subject LIKE '" + p + "%'",
                "DELETE FROM outbox WHERE intent_id IN " + intents,
                f"DELETE FROM jobs WHERE ref_id IN {intents} OR ref_id IN {latches}"
                f" OR ref_id IN {u}",
                "DELETE FROM retrieval_snapshot_rows WHERE intent_id IN " + intents,
                "DELETE FROM retrieval_snapshots WHERE id NOT IN (SELECT DISTINCT"
                " snapshot_id FROM retrieval_snapshot_rows)",
                f"DELETE FROM intents WHERE id IN {intents}",
                f"DELETE FROM users WHERE auth_subject LIKE '{p}%'",
            ]
        )
