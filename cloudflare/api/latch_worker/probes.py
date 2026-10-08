"""T1互換性probe本体(FastAPI app・CPython/Pyodide両方でimport可能)。

entry.py(Workers wiring)と分離してあり、backend/tests/cloudflare/
test_runtime_probe.py は CPython からも直接importして静的検査できる。

probeはすべてstub・外部送信なし(JWKSはself-fetchのmock経路のみ)。
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import json
import time
from datetime import UTC, datetime, timedelta, timezone
from decimal import Decimal

from fastapi import FastAPI, Request
from pydantic import BaseModel

from latch_worker.schema_probe import ParserOutput

JST = timezone(timedelta(hours=9))

# scheduled handlerの実行記録(entry.py が書き込み・probeが読む)
SCHEDULED_RUNS: list[dict] = []
QUEUE_MESSAGES: list[dict] = []


def _b64url(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).rstrip(b"=").decode()


def _b64url_decode(data: str) -> bytes:
    pad = "=" * (-len(data) % 4)
    return base64.urlsafe_b64decode(data + pad)


# -- HS256(対称鍵)の発行/検証 — tokens.py と同じ意味論の純Python実装 -----

HS_SECRET = "probe-hs-secret-0123456789abcdef"


def issue_hs256(claims: dict, *, secret: str = HS_SECRET) -> str:
    header = {"alg": "HS256", "typ": "JWT"}
    signing_input = (
        _b64url(json.dumps(header, separators=(",", ":")).encode())
        + "."
        + _b64url(json.dumps(claims, separators=(",", ":")).encode())
    )
    sig = hmac.new(secret.encode(), signing_input.encode(), hashlib.sha256).digest()
    return signing_input + "." + _b64url(sig)


def verify_hs256(token: str, *, secret: str = HS_SECRET) -> dict | None:
    """検証成功はclaimsを返す。失敗(改変・期限)はNone。"""
    try:
        header_b64, payload_b64, sig_b64 = token.split(".")
        header = json.loads(_b64url_decode(header_b64))
        if header.get("alg") != "HS256":
            return None
        signing_input = f"{header_b64}.{payload_b64}".encode()
        expected = hmac.new(secret.encode(), signing_input, hashlib.sha256).digest()
        if not hmac.compare_digest(expected, _b64url_decode(sig_b64)):
            return None
        claims = json.loads(_b64url_decode(payload_b64))
        exp = claims.get("exp")
        if isinstance(exp, (int, float)) and datetime.fromtimestamp(exp, tz=UTC) <= datetime.now(tz=UTC):
            return None
        return claims
    except Exception:  # noqa: BLE001
        return None


# -- RS256(非対称)の純Python検証 — pyjwt[crypto]の代替経路の実証 ----------

# DigestInfo prefix for SHA-256 (RFC 8017 §9.2)
_SHA256_DIGEST_INFO = bytes.fromhex("3031300d060960864801650304020105000420")


def verify_rs256(token: str, *, n: int, e: int) -> dict | None:
    """PKCS#1 v1.5 RS256検証の純Python実装(検証のみ・署名はしない)。

    cryptographyがPyodideで使えない場合の代替として、公開鍵(n, e)による
    modexpとパディング検査だけで済むことを実証する。
    """
    try:
        header_b64, payload_b64, sig_b64 = token.split(".")
        header = json.loads(_b64url_decode(header_b64))
        if header.get("alg") != "RS256":
            return None
        k = (n.bit_length() + 7) // 8
        s = int.from_bytes(_b64url_decode(sig_b64), "big")
        m = pow(s, e, n)
        em = m.to_bytes(k, "big")
        payload_bytes = _b64url_decode(payload_b64)
        digest = hashlib.sha256(f"{header_b64}.{payload_b64}".encode()).digest()
        expected = b"\x00\x01" + b"\xff" * (k - len(_SHA256_DIGEST_INFO) - len(digest) - 3) + b"\x00" + _SHA256_DIGEST_INFO + digest
        if not hmac.compare_digest(em, expected):
            return None
        claims = json.loads(payload_bytes)
        exp = claims.get("exp")
        if isinstance(exp, (int, float)) and datetime.fromtimestamp(exp, tz=UTC) <= datetime.now(tz=UTC):
            return None
        return claims
    except Exception:  # noqa: BLE001
        return None


# -- probe用の固定RSA鍵・トークン(CPython側で事前生成したtest資産) -------
# backend/tests/cloudflare/fixtures/rsa_probe.json と対。

RSA_PROBE = {
    # テスト専用RSA-2048公開鍵(backend/tests/cloudflare/fixtures/rsa_probe.json
    # と対・本番鍵ではない)。トークンは対応する秘密鍵でCPython側が署名済み。
    "n": int(
        "c37f25610fb2e4eca6f34f116462d0d269456f32475a5a3b5a49b577de6e5de4"
        "ceeedfe371f5a4c9218a2b57f3a2b799b026a366e0a0b31ba8e66b048531b361"
        "075cf12389339a6d67573b8bf789841d8e78749129fca975bb63ccf131ee9e2a"
        "06ceb1c3d1a21b33c52e0858be1cd9c399edc50eb80ec20c7c6e759feebe47c9"
        "b7416fbae14b5bdcd9dbdc10eaff0b2a95d7dee50f4167e66da67cd023a86dd8"
        "2c56660889637df996b3f37569eb605e759cc1e124a14c8fcb65dd77f6874232"
        "b09ac68bf1d4e01b6bcd51f84065fb2bfbb3143aaf70775abfa0649f722f5670"
        "cf0934ca9f0049f45fcaf19a082cfede73321a2ec4f3f5d61d7221d298cb6993"
        , 16
    ),
    "e": 65537,
}
RSA_PROBE_TOKEN = 'eyJhbGciOiJSUzI1NiIsImtpZCI6InByb2JlLXJzYS0xIiwidHlwIjoiSldUIn0.eyJzdWIiOiJwcm9iZS1ycy11c2VyIiwiaXNzIjoiaHR0cHM6Ly9pZHAuY2kubGF0Y2gudGVzdC9nb29nbGUiLCJhdWQiOiJsYXRjaC10ZXN0LWFwcCIsImV4cCI6OTk5OTk5OTk5OX0.Fv_C4xPjyK094pd5jPSqYzezcxVHiN9e_t_UWwRCBFioPC96EVIzuBZxRquB9ugjE6FV4vduYoAGEIVpHHI7lO_af_5oTnS5AARE4w2iY8t0dtUMllsWab7oSreVvHt0OGbwbyAbzW9wh23OBfv8tVc8ye7loCChjculowrZu4qo0nNzJaCAHqq_QQbEFaDn46AaRYuqhFFvLqgmCcm55rljZtRNX3gaeP_0F5mf7Lzt_q_e2djuKsLXvSyRoMFja1u0Tb71iTBzXJq1rmQtK6Yg9YUba1_smQYkR_CKjlUU78oqSwsIwOnF98VgUdaY15j7ERDtaZFna5dsb0jQrQ'


class ProbeBody(BaseModel):
    text: str = ""


def create_app() -> FastAPI:
    app = FastAPI(title="LATCH T1 probe")

    @app.get("/health")
    async def health() -> dict:
        return {"status": "ok", "probe": True}

    @app.get("/probe/report")
    async def report() -> dict:
        import sys

        def try_import(name: str) -> str:
            try:
                __import__(name)
                return "ok"
            except Exception as exc:  # noqa: BLE001
                return f"fail:{type(exc).__name__}"

        def version_of(name: str) -> str | None:
            try:
                from importlib import metadata

                return metadata.version(name)
            except Exception:  # noqa: BLE001
                return None

        checks = {
            "hashlib": try_import("hashlib"),
            "hmac": try_import("hmac"),
            "decimal": try_import("decimal"),
            "uuid": try_import("uuid"),
            "sqlite3": try_import("sqlite3"),
            "asyncio": try_import("asyncio"),
            "cryptography": try_import("cryptography"),
            "jwt": try_import("jwt"),
            "anthropic": try_import("anthropic"),
            "google.genai": try_import("google.genai"),
            "typesafe_sdk": try_import("typesafe_sdk"),
            "yaml": try_import("yaml"),
        }
        return {
            "python": sys.version,
            "implementation": sys.implementation.name,
            "fastapi": version_of("fastapi"),
            "pydantic": version_of("pydantic"),
            "import_checks": checks,
            "scheduled_runs": len(SCHEDULED_RUNS),
            "queue_messages": len(QUEUE_MESSAGES),
        }

    @app.get("/probe/datetime")
    async def datetime_probe() -> dict:
        fixed = datetime(2026, 10, 8, 15, 30, 0, tzinfo=UTC)
        jst = fixed.astimezone(JST)
        parsed = datetime.fromisoformat("2026-10-08T19:00:00+09:00")
        return {
            "fixed_utc": fixed.isoformat(),
            "fixed_jst": jst.isoformat(),
            "jst_offset": jst.utcoffset().total_seconds(),
            "jst_weekday": jst.weekday(),
            "parse_roundtrip": parsed.astimezone(UTC).isoformat(),
            "add_day_over_month_end": (
                datetime(2026, 10, 31, 23, 0, 0, tzinfo=JST) + timedelta(hours=2)
            ).isoformat(),
        }

    @app.get("/probe/decimal")
    async def decimal_probe() -> dict:
        d = Decimal("0.1") + Decimal("0.2")
        q = (Decimal("1.005") * Decimal("1")).quantize(Decimal("0.01"))
        return {
            "sum": str(d),
            "sum_eq_float": d == Decimal("0.3"),
            "quantize": str(q),
            "negative_zero": str(Decimal("-0.0")),
        }

    @app.post("/probe/schema")
    async def schema_probe(body: ProbeBody) -> dict:
        """vendored ParserOutputで既存schemaと同じ検証を通す。"""
        payload = {
            "category": {"primary": "meal"},
            "alcohol_involved": False,
            "time": {"start": "2026-10-08T19:00:00+09:00"},
            "location": {"name": "東京駅"},
            "budget": {"currency": "JPY"},
            "participants": {},
            "unknown_extra_key": 1,  # 余分キーは無視(07 §4)
        }
        out = ParserOutput.model_validate(payload)
        tz_naive = {
            "category": {"primary": "meal"},
            "alcohol_involved": False,
            "time": {"start": "2026-10-08T19:00:00"},  # tz無し → 検証失敗
            "location": {"name": "x"},
            "budget": {"currency": "JPY"},
            "participants": {},
        }
        rejected = False
        try:
            ParserOutput.model_validate(tz_naive)
        except Exception:  # noqa: BLE001
            rejected = True
        return {
            "validated": out.model_dump(mode="json"),
            "tz_naive_rejected": rejected,
            "echo_len": len(body.text),
        }

    @app.get("/probe/jwt_hs")
    async def jwt_hs_probe() -> dict:
        now = int(time.time())
        token = issue_hs256({"sub": "probe-user", "exp": now + 600, "jti": "j-1"})
        ok = verify_hs256(token)
        tampered = token[:-3] + ("AAA" if not token.endswith("AAA") else "BBB")
        tampered_result = verify_hs256(tampered)
        expired = issue_hs256({"sub": "probe-user", "exp": now - 10})
        return {
            "verified_sub": ok["sub"] if ok else None,
            "tampered_rejected": tampered_result is None,
            "expired_rejected": verify_hs256(expired) is None,
        }

    @app.post("/probe/jwt_rs")
    async def jwt_rs_probe(body: dict) -> dict:
        """body.token をRSA_PROBE鍵で純Python検証(CPython側で署名した資産)。"""
        token = body.get("token", "")
        claims = verify_rs256(token, n=RSA_PROBE["n"], e=RSA_PROBE["e"])
        tampered = token[:-3] + ("AAA" if not token.endswith("AAA") else "BBB")
        return {
            "verified_sub": claims["sub"] if claims else None,
            "tampered_rejected": verify_rs256(tampered, n=RSA_PROBE["n"], e=RSA_PROBE["e"]) is None,
        }

    @app.get("/probe/d1")
    async def d1_probe(request: Request) -> dict:
        """D1 bindingのprepare/first/batch・atomic batchの実証。"""
        env = request.scope["env"]
        db = env.DB
        row = await db.prepare("SELECT 1 + 1 AS v").first()
        await db.batch([
            db.prepare(
                "CREATE TABLE IF NOT EXISTS probe_t"
                " (id TEXT PRIMARY KEY, value TEXT, n INTEGER)"
            ),
            db.prepare(
                "INSERT OR REPLACE INTO probe_t (id, value, n)"
                " VALUES ('p1', 'hello', 42)"
            ),
        ])
        got = await db.prepare("SELECT value, n FROM probe_t WHERE id = 'p1'").first()
        # 条件付きUPDATE(0行でもSQLエラーにならないこと — §3.2)
        upd = await db.prepare(
            "UPDATE probe_t SET n = n + 1 WHERE id = 'nonexistent'"
        ).run()
        return {
            "select_first": dict(row) if row else None,
            "batch_row": dict(got) if got else None,
            "zero_row_update_ok": True,
            "meta_changes": getattr(getattr(upd, "meta", None), "changes", None),
        }

    @app.get("/probe/env")
    async def env_probe(request: Request) -> dict:
        env = request.scope["env"]
        return {
            "latch_env": env.LATCH_ENV,
            "probe_text_var": env.PROBE_TEXT_VAR,
            "secret_probe_present": hasattr(env, "SECRET_PROBE"),
            "db_binding": hasattr(env, "DB"),
            "jobs_binding": hasattr(env, "JOBS"),
        }

    @app.get("/probe/queue")
    async def queue_probe(request: Request) -> dict:
        """Queues producerの実証(local sim)。consumerはDefault.queueで受ける。"""
        env = request.scope["env"]
        await env.JOBS.send(
            {
                "event_id": "probe-1",
                "reason": "t1_probe",
                "payload_schema_version": 1,
            }
        )
        return {"sent": True}

    @app.get("/probe/queue_drain")
    async def queue_drain_probe() -> dict:
        """queue consumerが受信したmessageの一覧(local検証用)。"""
        return {"messages": QUEUE_MESSAGES[-10:]}

    @app.post("/probe/jwt_rs_pyjwt")
    async def jwt_rs_pyjwt_probe(body: dict) -> dict:
        """pyjwt+cryptography でのRS256検証(従来経路がWorkersで動くか)。"""
        import jwt as pyjwt

        token = body.get("token", "")
        jwk = body.get("jwk", {})
        try:
            key = pyjwt.PyJWK.from_dict(jwk, algorithm="RS256")
            claims = pyjwt.decode(
                token,
                key.key,
                algorithms=["RS256"],
                audience=body.get("audience"),
                options={"verify_exp": False, "verify_iat": False},
            )
            verified_sub = claims.get("sub")
            pyjwt_ok = True
            error = None
        except Exception as exc:  # noqa: BLE001
            verified_sub = None
            pyjwt_ok = False
            error = f"{type(exc).__name__}: {exc}"
        return {"verified_sub": verified_sub, "ok": pyjwt_ok, "error": error}

    @app.get("/probe/jwks_fetch")
    async def jwks_fetch_probe(request: Request) -> dict:
        """JWKS取得経路の非同期fetch実証(thread非依存)。

        外部送信しない: 自身の /probe/jwks_mock をfetchする。
        本番ではurlのみ差し替え(asyncio.to_thread を使わない)。
        """
        from workers import fetch  # Pyodide側のみで提供される

        origin = request.url.scheme + "://" + request.url.netloc
        resp = await fetch(f"{origin}/probe/jwks_mock")
        body = json.loads(await resp.text())
        return {
            "status": resp.status,
            "kids": [k["kid"] for k in body["keys"]],
            "thread_free": True,
        }

    @app.get("/probe/jwks_mock")
    async def jwks_mock() -> dict:
        """jwks_fetch_probeの取得先(self-fetch用mock・外部送信なし)。"""
        n_b64 = _b64url(RSA_PROBE["n"].to_bytes((RSA_PROBE["n"].bit_length() + 7) // 8, "big"))
        return {"keys": [{"kty": "RSA", "kid": "probe-rsa-1", "n": n_b64, "e": "AQAB"}]}

    @app.get("/probe/state")
    async def state_probe(mark: str = "") -> dict:
        """並行リクエストの状態分離検証用(グローバルを経由しない)。"""
        await asyncio_sleep(0.05)
        return {"mark": mark, "leak": None}

    @app.get("/probe/mem")
    async def mem_probe(mb: int = 8) -> dict:
        data = bytearray(mb * 1024 * 1024)
        data[::4096] = b"\x01" * len(data[::4096])
        return {"allocated_mb": mb, "ok": True}

    @app.get("/probe/cpu")
    async def cpu_probe(iterations: int = 200_000) -> dict:
        start = time.perf_counter()
        total = 0
        for i in range(iterations):
            total += i * i
        duration_ms = (time.perf_counter() - start) * 1000
        return {"iterations": iterations, "duration_ms": round(duration_ms, 2), "total": total}

    return app


async def asyncio_sleep(seconds: float) -> None:
    import asyncio

    await asyncio.sleep(seconds)
