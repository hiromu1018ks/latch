"""ws-4受け入れハーネスの基盤(M4 ws-4・ws-3ハーネス土台の再利用)。

証拠YAMLは本conftestのsessionfinishのみが出力する(hookはpytestの
conftest収集経路のみ発火するため、sys.path importで読まれたstaging_tests
のconftestのsessionfinishは発火しない → 証拠はws4-接頭辞で完全分離)。
実行は `make staging-obs-test`。
"""

import importlib
import sys
from pathlib import Path

OBS_DIR = Path(__file__).resolve().parent
STAGING_TESTS_DIR = OBS_DIR.parent / "staging_tests"
# --import-mode=importlibでは収集ディレクトリがsys.pathに入らないため
# obs_resultsとhelpers両方の解決先を明示する
if str(OBS_DIR) not in sys.path:
    sys.path.insert(0, str(OBS_DIR))
if str(STAGING_TESTS_DIR) not in sys.path:
    sys.path.insert(0, str(STAGING_TESTS_DIR))

# 同名conftest問題の解決: pytestが登録したobs側のconftest("conftest")を
# sys.modulesから外し、staging_tests側を確実にimportさせる。以後の
# helpers_seed等の `from conftest import ...` はstaging_tests側を指す
import conftest as _obs_conftest  # noqa: E402,F401  (pytestが読んだ自分自身)

if (
    Path(getattr(sys.modules.get("conftest"), "__file__", "")).parent
    != STAGING_TESTS_DIR
):
    del sys.modules["conftest"]
st_conftest = importlib.import_module("conftest")  # staging_tests/conftest.py

from datetime import datetime, timedelta, timezone  # noqa: E402

import asyncpg  # noqa: E402
import httpx  # noqa: E402
import pytest  # noqa: E402
import yaml  # noqa: E402
from obs_results import _results  # noqa: E402  (ws-4独自の証拠収集)

API_BASE = st_conftest.API_BASE
DSN = st_conftest.DSN
EXPECTED_HEAD = st_conftest.EXPECTED_HEAD
RESULTS_DIR = Path(__file__).resolve().parents[2] / "docs" / "testassets" / "results"


def pytest_sessionfinish(session, exitstatus):  # noqa: ANN001
    """session終了時に証拠YAMLを出力する(失敗時のitemsも含めて出す)。"""
    jst = timezone(timedelta(hours=9))
    now = datetime.now(jst)
    RESULTS_DIR.mkdir(parents=True, exist_ok=True)
    out = {
        "unit": "M4-ws-4",
        "executed_at": now.isoformat(),
        "environment": "latch-staging",
        "items": list(_results),
    }
    ts = now.strftime("%Y%m%d-%H%M%S")
    path = RESULTS_DIR / f"ws4-staging-result-{ts}.yaml"
    path.write_text(
        yaml.safe_dump(out, allow_unicode=True, sort_keys=False), encoding="utf-8"
    )


@pytest.fixture
async def api():
    async with httpx.AsyncClient(base_url=API_BASE, timeout=30.0) as c:
        yield c


@pytest.fixture
async def db():
    conn = await asyncpg.connect(DSN)
    # jsonbはstrで返りうる(asyncpg・コードベース規約)ためstaging_testsの
    # db fixtureと同一のクライアント側codecで解く(検査はdict/list)
    import json

    await conn.set_type_codec(
        "jsonb", encoder=json.dumps, decoder=json.loads, schema="pg_catalog"
    )
    await conn.execute("SET default_transaction_read_only = on")
    try:
        yield conn
    finally:
        await conn.close()
