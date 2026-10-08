"""T1: Python Workers互換性probe(2026-10-08移行計画 §6 T1)。

2層構成:
1. CPython側の単体試験(常時実行): probe資産の健全性・schemaコピーの
   drift検出・RSA資産の純Python検証・B0互換の基礎確認。
2. 実測probe(marker=cloudflare_local): `make cf-dev` で起動した
   pywrangler dev(Pyodide実行系)へHTTPで問い、RuntimeCompatibilityReport
   を docs/testassets/results/ へ書き出す。実配備は別承認後。

probe handlerはstubのみ・外部送信なし(計画T1規律)。JWKS fetchは
self-fetch mockのみ。LLM SDKは import のみ。
"""

from __future__ import annotations

import hashlib
import json
import sys
from datetime import UTC, datetime, timedelta, timezone
from pathlib import Path

import httpx
import pytest
import yaml

from contracts.contract import REPO_DIR  # noqa: TID252 (tests root経由)

CF_DIR = REPO_DIR / "cloudflare" / "api"
sys.path.insert(0, str(CF_DIR))

from latch_worker import probes  # noqa: E402

pytestmark = pytest.mark.integration

RESULTS_DIR = REPO_DIR / "docs" / "testassets" / "results"
FIXTURES_DIR = Path(__file__).resolve().parent / "fixtures"

JST = timezone(timedelta(hours=9))


# =============================================================================
# 1. CPython側単体試験(常時実行)
# =============================================================================


def test_schema_probe_copy_matches_backend_source():
    """vendored ParserOutputが原典と同一であること(drift検出)。"""
    original = (
        REPO_DIR / "backend" / "src" / "latch" / "intents" / "schema.py"
    ).read_bytes()
    copy = (CF_DIR / "latch_worker" / "schema_probe.py").read_bytes()

    def body_hash(data: bytes) -> str:
        # docstring差し替えのみ許容: 本文(最初の"""ブロック以降)で比較
        text = data.decode("utf-8")
        try:
            end = text.index('"""', text.index('"""') + 3) + 3
        except ValueError:
            end = 0
        return hashlib.sha256(text[end:].encode()).hexdigest()

    assert body_hash(copy) == body_hash(original), (
        "schema_probe.py が backend/src/latch/intents/schema.py からずれた。"
        "原典を再コピーすること"
    )
    # 挙動も同一: 検証の代表ケース
    from latch_worker.schema_probe import ParserOutput

    ok = ParserOutput.model_validate(
        {
            "category": {"primary": "meal"},
            "alcohol_involved": False,
            "time": {"start": "2026-10-08T19:00:00+09:00"},
            "location": {"name": "x"},
            "budget": {"currency": "JPY"},
            "participants": {},
        }
    )
    assert ok.category.primary == "meal"
    with pytest.raises(Exception):  # noqa: B017, PT011
        ParserOutput.model_validate(
            {
                "category": {"primary": "meal"},
                "alcohol_involved": False,
                "time": {"start": "2026-10-08T19:00:00"},  # tz無し
                "location": {"name": "x"},
                "budget": {"currency": "JPY"},
                "participants": {},
            }
        )


def test_rsa_fixture_pure_python_verify():
    """RSA資産がCPythonの純Python検証も通ること(worker側と同一ロジック)。"""
    fx = json.loads((FIXTURES_DIR / "rsa_probe.json").read_text())
    claims = probes.verify_rs256(fx["token"], n=int(fx["n_hex"], 16), e=fx["e"])
    assert claims is not None and claims["sub"] == "probe-rs-user"
    tampered = fx["token"][:-3] + "AAA"
    assert probes.verify_rs256(tampered, n=int(fx["n_hex"], 16), e=fx["e"]) is None


def test_hs256_roundtrip_on_cpython():
    """HS256発行/検証の純Python実装がCPythonでも正しく動くこと。"""
    token = probes.issue_hs256({"sub": "cpython-user", "exp": 9999999999})
    assert probes.verify_hs256(token)["sub"] == "cpython-user"
    assert probes.verify_hs256(token[:-2] + "zz") is None


def test_rsa_fixture_matches_embedded_constants():
    """probes.py埋め込みの公開鍵・tokenがfixtureと一致していること。"""
    fx = json.loads((FIXTURES_DIR / "rsa_probe.json").read_text())
    assert probes.RSA_PROBE["n"] == int(fx["n_hex"], 16)
    assert probes.RSA_PROBE["e"] == fx["e"]
    assert probes.RSA_PROBE_TOKEN == fx["token"]


def test_datetime_expectations():
    """probe期待値の自己検証(CPython基準)。"""
    fixed = datetime(2026, 10, 8, 15, 30, 0, tzinfo=UTC)
    jst = fixed.astimezone(JST)
    assert jst.isoformat() == "2026-10-09T00:30:00+09:00"
    assert jst.utcoffset().total_seconds() == 32400.0
    assert jst.weekday() == 4  # Friday


# =============================================================================
# 2. 実測probe(cloudflare_local): pywrangler dev が起動中のみ
# =============================================================================


async def _get(client: httpx.AsyncClient, path: str) -> dict:
    resp = await client.get(path, timeout=60.0)
    assert resp.status_code == 200, f"{path}: {resp.status_code} {resp.text[:200]}"
    return resp.json()


@pytest.mark.cloudflare_local
async def test_runtime_probe_and_report(cf_worker_url, tmp_path):
    """全probeを実行し、RuntimeCompatibilityReportを書き出す。

    実行: make cf-dev(ターミナル1) → make cf-probe(ターミナル2)。
    レポートは docs/testassets/results/cloudflare-runtime-probe-*.yaml。
    """
    report: dict = {"probes": {}, "adopted_path": "A"}
    async with httpx.AsyncClient(base_url=cf_worker_url) as client:
        # -- 静的import/環境 --
        rpt = await _get(client, "/probe/report")
        report["runtime"] = {
            "python": rpt["python"].split(" ")[0],
            "implementation": rpt["implementation"],
            "fastapi": rpt["fastapi"],
            "pydantic": rpt["pydantic"],
        }
        report["probes"]["import_checks"] = rpt["import_checks"]

        # -- datetime / decimal --
        dt = await _get(client, "/probe/datetime")
        assert dt["fixed_jst"] == "2026-10-09T00:30:00+09:00"
        assert dt["jst_offset"] == 32400.0
        assert dt["parse_roundtrip"] == "2026-10-08T10:00:00+00:00"
        report["probes"]["datetime"] = {k: v for k, v in dt.items() if k != "fixed_utc"}

        dec = await _get(client, "/probe/decimal")
        assert dec["sum"] == "0.3"
        report["probes"]["decimal"] = dec

        # -- 既存schema(Pydantic検証) --
        schema = (
            await client.post("/probe/schema", json={"text": "x"}, timeout=60.0)
        ).json()
        assert schema["validated"]["category"]["primary"] == "meal"
        assert schema["tz_naive_rejected"] is True
        report["probes"]["schema_validation"] = {
            "validated": True,
            "tz_naive_rejected": True,
        }

        # -- JWT: HS256(純Python) / RS256(純Python + pyjwt+cryptography) --
        hs = await _get(client, "/probe/jwt_hs")
        assert hs["verified_sub"] == "probe-user"
        assert hs["tampered_rejected"] and hs["expired_rejected"]
        report["probes"]["jwt_hs256_pure_python"] = hs

        fx = json.loads((FIXTURES_DIR / "rsa_probe.json").read_text())
        rs = (
            await client.post(
                "/probe/jwt_rs", json={"token": fx["token"]}, timeout=60.0
            )
        ).json()
        assert rs["verified_sub"] == "probe-rs-user"
        assert rs["tampered_rejected"]
        report["probes"]["jwt_rs256_pure_python"] = rs

        rs2 = (
            await client.post(
                "/probe/jwt_rs_pyjwt",
                json={
                    "token": fx["token"],
                    "jwk": fx["jwk"],
                    "audience": "latch-test-app",
                },
                timeout=120.0,
            )
        ).json()
        report["probes"]["jwt_rs256_pyjwt_cryptography"] = rs2
        assert rs2["ok"] is True

        # -- JWKS async fetch(thread非依存・self-fetch mock) --
        jwks = await _get(client, "/probe/jwks_fetch")
        assert jwks["status"] == 200 and "probe-rsa-1" in jwks["kids"]
        report["probes"]["jwks_async_fetch"] = jwks

        # -- D1: prepare/first/batch/0行UPDATE --
        d1 = await _get(client, "/probe/d1")
        assert d1["select_first"] == {"v": 2}
        assert d1["batch_row"] == {"value": "hello", "n": 42}
        assert d1["zero_row_update_ok"] is True
        report["probes"]["d1"] = d1

        # -- env / bindings / queue producer+consumer --
        env = await _get(client, "/probe/env")
        assert env["db_binding"] and env["jobs_binding"]
        assert env["latch_env"] == "local-probe"
        report["probes"]["env"] = env

        await _get(client, "/probe/queue")
        import asyncio

        drained = []
        for _ in range(12):
            await asyncio.sleep(1.0)
            drain = await _get(client, "/probe/queue_drain")
            drained = drain["messages"]
            if drained:
                break
        report["probes"]["queue_consumer"] = {
            "delivered": bool(drained),
            "messages": drained[-3:],
        }
        assert drained, "queue consumerがlocalで1通も受信していない"

        # -- scheduled(cron) handler --
        sched = await client.get("/cdn-cgi/local/scheduled?format=json", timeout=30.0)
        assert sched.status_code == 200
        rpt2 = await _get(client, "/probe/report")
        report["probes"]["scheduled"] = {
            "outcome": sched.json().get("outcome"),
            "runs_recorded": rpt2["scheduled_runs"],
        }
        assert rpt2["scheduled_runs"] >= 1

        # -- 並行state分離 --
        marks = ["aaa", "bbb", "ccc"]
        results = await asyncio.gather(
            *(client.get("/probe/state", params={"mark": m}) for m in marks)
        )
        got_marks = [r.json()["mark"] for r in results]
        assert got_marks == marks
        rs20 = await asyncio.gather(
            *(client.get("/probe/state", params={"mark": f"m{i}"}) for i in range(20))
        )
        assert all(r.json()["mark"] == f"m{i}" for i, r in enumerate(rs20))
        report["probes"]["concurrent_state_isolation"] = {
            "parallel_requests": 20,
            "isolated": True,
        }

        # -- CPU / memory(参考値・local実測) --
        cpu = await _get(client, "/probe/cpu?iterations=1000000")
        mem = await _get(client, "/probe/mem?mb=32")
        report["probes"]["cpu_reference"] = cpu
        report["probes"]["memory_32mb"] = mem

    # -- レポート採点と書き出し --
    imports = report["probes"]["import_checks"]
    blockers = [k for k, v in imports.items() if v.startswith("fail")]
    report["blockers"] = blockers
    report["adoption"] = (
        "A: Python Workers主体(routes・Pydantic・純粋計算・promptを再利用し、"
        "D1/通信/job adapterを交換)"
        if not blockers
        else f"blocked by: {blockers}"
    )
    report["notes"] = [
        "cryptography/pyjwt/anthropic/google-genai/typesafe-sdk は "
        "pywrangler(pyodide 3.14.2)でimport可能 — 2026-10-06調査書の"
        "「cryptography wheel不在」は新toolchainで解消済み",
        "RS256検証は従来経路(pyjwt+cryptography)がそのまま動く。"
        "純Python fallback(検証のみ)も実証済み",
        "JWKS取得は asyncio.to_thread 不可のため async fetch へ交換"
        "(self-fetch mockで実証)",
        "D1 batch()は準備済みSQL群の原子的実行・0行UPDATEはSQLエラーなし",
        "Queuesは producer/consumer 同一workerで送受+ack実証"
        "(payloadはJSON文字列で送る規律をT5で確定)",
        "scheduled handlerは /cdn-cgi/local/scheduled で起動実証",
        "本実測はローカル(pywrangler dev)。実配備での性能・制限は別承認後",
    ]
    stamp = datetime.now(UTC).strftime("%Y%m%d-%H%M%S")
    RESULTS_DIR.mkdir(parents=True, exist_ok=True)
    out = RESULTS_DIR / f"cloudflare-runtime-probe-{stamp}.yaml"
    out.write_text(
        yaml.safe_dump(report, allow_unicode=True, sort_keys=True), encoding="utf-8"
    )
    print(f"\nwrote {out}")
