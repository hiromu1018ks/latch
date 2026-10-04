"""stagingハーネス共通(M4 ws-3・10 §1のstaging観測経路)。

tests/配下ではなく独立ディレクトリ(pyprojectのtestpaths=["tests"]の外側)
のため既存のmake test/test-ciからは収集されない。実行: `cd backend &&
uv run pytest staging_tests`(前提: make staging-resetからのフル起動)。
session状態はモジュール辞書+ヘルパー関数で持つ(sessionスコープのasync
fixtureを使わない — pytest-asyncioのloop scope結合を避ける)。
"""

from __future__ import annotations

import json
from pathlib import Path

import asyncpg
import httpx
import pytest
import yaml

from latch.auth.tools import issue_idp_token
from latch.core.clock import ControlledClock

REPO_ROOT = Path(__file__).resolve().parents[2]  # backend/staging_tests → repoルート
API_BASE = "http://127.0.0.1:18000"  # nginx(設計§2.3観測経路(a))
DSN = "postgresql://latch:latch@127.0.0.1:15432/latch"  # DB読取(SELECT専用)
REDIS_URL = "redis://127.0.0.1:16379/0"  # ControlledClock
KEYS_DIR = REPO_ROOT / ".staging" / "keys"
SEED_PATH = REPO_ROOT / "docs" / "testassets" / "m4-seed.yaml"
RESULTS_DIR = REPO_ROOT / "docs" / "testassets" / "results"
EXPECTED_HEAD = "0008"  # ws-4が0008を追加した後のhead(M4 ws-4)

# --- 証拠収集(10 §5の形式・sessionfinishでYAML出力) ---

_results: list[dict] = []


def record_item(
    *, item_id: str, name: str, test: str, verdict: str, observed: dict
) -> None:
    """証拠1件を収集する(試験ID・入力条件・合否・観測値)。"""
    _results.append(
        {
            "id": item_id,
            "name": name,
            "test": test,
            "verdict": verdict,
            "observed": observed,
        }
    )


def pytest_sessionfinish(session, exitstatus):  # noqa: ANN001
    """session終了時に証拠YAMLを出力する(失敗時のitemsも含めて出す)。"""
    from datetime import datetime, timedelta, timezone

    jst = timezone(timedelta(hours=9))
    now = datetime.now(jst)
    RESULTS_DIR.mkdir(parents=True, exist_ok=True)
    out = {
        "unit": "M4-ws-3",
        "executed_at": now.isoformat(),
        "environment": "latch-staging",
        "items": _results,
    }
    ts = now.strftime("%Y%m%d-%H%M%S")
    path = RESULTS_DIR / f"ws3-staging-result-{ts}.yaml"
    path.write_text(
        yaml.safe_dump(out, allow_unicode=True, sort_keys=False), encoding="utf-8"
    )


# --- fixtures(functionスコープ・sessionスコープasyncは使わない) ---


@pytest.fixture
async def api():
    async with httpx.AsyncClient(base_url=API_BASE, timeout=30.0) as c:
        yield c


@pytest.fixture
async def db():
    conn = await asyncpg.connect(DSN)
    # asyncpgはjsonbをstrで返す(コードベース規約: worker/matching/layer3.py
    # 「jsonb列はstrで返りうるためjson.loadsで解釈する」)。検査コードは
    # dict/listとして読むためクライアント側codecで解く(サーバ側の変更なし・
    # SELECT専用の規律は不変 — 計画コードからの適応・報告書に記録)
    await conn.set_type_codec(
        "jsonb", encoder=json.dumps, decoder=json.loads, schema="pg_catalog"
    )
    await conn.execute("SET default_transaction_read_only = on")
    try:
        yield conn
    finally:
        await conn.close()


# --- 共通ヘルパ(キャッシュはモジュール辞書) ---

_clock = None


async def get_clock() -> ControlledClock:
    """staging Redis基準の共有制御Clock(clock_cliと同一コード経路)。"""
    global _clock
    if _clock is None:
        import redis as redis_sync

        _clock = ControlledClock(
            redis_sync.Redis.from_url(REDIS_URL, decode_responses=True)
        )
    return _clock


def issue_token(subject: str) -> str:
    """staging鍵でIdP JWTを発行し/v1/auth/tokenでaccess_tokenへ交換する。

    保護エンドポイントのBearerはアクセストークン(HS256・auth/service.
    authenticate)でありIdP JWT(RS256)は直接使えないため、ws-1と同一の
    交換経路(POST /v1/auth/token)を踏む(計画コードからの適応・報告書に
    記録)。交換はユーザー行を作らない(profile_completeはUser行の存在から
    導出のみ)ためシード投入と干渉しない。

    キャッシュしない(仮想Clock前進対策): access tokenのexpは発行時の
    仮想時刻+1h(ACCESS_TTL_S)であり、Clock前進(S1#8で+13時間・S3で
    D+6)で即失効する。IdPトークン側はexpires_in=30日(REFRESH_TTL_S相当)
    で発行し、仮想時刻が最終前進値(base+6日)を超えても交換が通るように
    する。1呼び出し毎の交換コストはローカル数十ms・auth制限
    (60req/分/subject)に対して余裕。
    """
    pem = (KEYS_DIR / "idp_test_private.pem").read_bytes()
    idp_token = issue_idp_token(
        provider="google",
        subject=subject,
        private_key_pem=pem,
        expires_in=30 * 24 * 3600,
    )
    r = httpx.post(
        f"{API_BASE}/v1/auth/token",
        json={"provider": "google", "idp_token": idp_token},
        timeout=30.0,
    )
    if r.status_code != 200:
        raise AssertionError(f"auth/token交換失敗 {subject}: {r.status_code} {r.text}")
    return r.json()["access_token"]


def auth_header(subject: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {issue_token(subject)}"}


async def ensure_env_prerequisites(db) -> None:  # noqa: ANN001
    """環境前提の名前つき失敗(設計§4-2 — 原因不明の連鎖失敗を防ぐ)。"""
    # API疎通はfixture apiの初期接続エラーで判るためここではDB/geo/鍵/headのみ
    ver = await db.fetchval("SELECT version_num FROM alembic_version")
    if ver != EXPECTED_HEAD:
        raise AssertionError(
            f"alembic headが{EXPECTED_HEAD}ではありません(実際={ver})。"
            "ws-5マージ後のmainからworktreeを作り・make staging-reset(または"
            "staging-migrate)を再実行してください"
        )
    n_geo = await db.fetchval("SELECT count(*) FROM geofeatures")
    if not n_geo:
        raise AssertionError(
            "geofeaturesが空です。make staging-geo-importを実行してください"
        )
    if not (KEYS_DIR / "idp_test_private.pem").exists():
        raise AssertionError(".staging/keysがありません。make staging-keysを実行")


def publish_created(intent_id: str, *, version: int = 1) -> None:
    """Match Event(created)を手動発行する(10 §1「疑似イベント注入」)。

    make staging-publishと同一経路(python -m latch.events publish・
    EventBus.publish_match_event)。グループ生成はイベント処理時の
    embedding揃いに依存し、Pool空のまま再試行されない(実測・報告書に
    記録)ため、embedding完備を確認したうえでcreatedを再発行し
    GroupEngine.handleを再実行させる。
    """
    import os
    import subprocess

    env = dict(os.environ)
    env["LATCH_PUBSUB_EMULATOR_HOST"] = "127.0.0.1:18085"
    env["LATCH_PUBSUB_PROJECT_ID"] = "latch-staging"
    r = subprocess.run(
        [
            "uv",
            "run",
            "python",
            "-m",
            "latch.events",
            "publish",
            "created",
            "--intent-id",
            str(intent_id),
            "--version",
            str(version),
        ],
        cwd=REPO_ROOT / "backend",
        env=env,
        capture_output=True,
        text=True,
        check=False,
    )
    if r.returncode != 0:
        raise AssertionError(
            f"created再発行失敗 intent={intent_id}: {r.returncode} {r.stderr}"
        )
