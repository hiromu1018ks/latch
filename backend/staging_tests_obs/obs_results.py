"""ws-4受け入れの証拠収集とprometheus照合(10 §5形式・staging_tests_obs共用)。

証拠収集は本モジュールに一本化する(staging_tests/conftest.pyのrecord_itemと
同名問題を避けるため独立モジュール。sessionfinishはconftest.pyから
`from obs_results import _results` で参照する)。
"""

import asyncio
from datetime import datetime, timedelta, timezone

import httpx

PROM_URL = "http://127.0.0.1:19090"
GRAFANA_URL = "http://127.0.0.1:13000"

_results: list[dict] = []


def record_item(
    *, item_id: str, name: str, test: str, verdict: str, observed: dict
) -> None:
    _results.append(
        {
            "id": item_id,
            "name": name,
            "test": test,
            "verdict": verdict,
            "observed": observed,
        }
    )


async def prom_query(expr: str) -> list[dict]:
    """Prometheusの即時クエリ(結果seriesのlist・空なら[])。"""
    async with httpx.AsyncClient(timeout=10.0) as client:
        r = await client.get(f"{PROM_URL}/api/v1/query", params={"query": expr})
    assert r.status_code == 200, f"prometheus query failed: {r.text}"
    return r.json()["data"]["result"]


async def wait_prom_series(expr: str, timeout_s: float = 120.0) -> list[dict]:
    """scrape周期(15秒)を考慮してseriesが現れるまで待つ。"""
    jst = timezone(timedelta(hours=9))
    deadline = datetime.now(jst).timestamp() + timeout_s
    while datetime.now(jst).timestamp() < deadline:
        result = await prom_query(expr)
        if result:
            return result
        await asyncio.sleep(5.0)
    raise AssertionError(f"prometheus series not found within {timeout_s}s: {expr}")
