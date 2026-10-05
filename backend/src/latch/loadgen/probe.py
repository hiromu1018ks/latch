"""合成監視プローブ(10 §4.3・M4 ws-7 design §2.1案A)。

nginx入口へGET /health(認証外)とGET /v1/users/me(認証付き)を打つ。合否対象
は1分間隔(main)側のみ・failover精度観察用の10秒間隔(dense)を併設する
(合否判定外)。成功率=成功プローブ数÷総プローブ数・計画停止のサンプルは
マーカーファイル(実行中タグ記入・§2.1読み替え2)の存在でtaggedとし分母外
(承認事項§5-2)。タイムアウトは10秒(nginxのproxy_next_upstream再送が収まる
値)。トークンはsubjectのaccess_tokenをキャッシュし・発行から50分経過で
再発行する(access token TTL=1時間と試験窓60分の一致を避ける)。
実行時刻はClock経由(C2)・待機はasyncio.sleep(stub.pyと同一規律)。
"""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

import httpx

from latch.auth.tools import issue_idp_token
from latch.core.clock import Clock

MAIN_INTERVAL_S = 60.0
DENSE_INTERVAL_S = 10.0
REQUEST_TIMEOUT_S = 10.0
TOKEN_REISSUE_SEC = 50 * 60.0
PASS_RATE_THRESHOLD = 0.995  # 01 §20(機械判定・正式合否はG4の人間側)
PROBE_PATHS = ("/health", "/v1/users/me")
# マーカーの既定位置(ファイル位置基準・cwd非依存。loadgenのSEED_DIR教訓)
REPO_ROOT = Path(__file__).resolve().parents[4]
DEFAULT_MARKER = REPO_ROOT / ".staging" / "probe-outage.marker"


@dataclass(frozen=True)
class ProbeSample:
    """1リクエストの記録(1プローブ=1リクエスト・集計はmain側合計)。"""

    kind: str  # "main" | "dense"
    at: datetime
    path: str
    ok: bool
    error: str | None
    tagged_planned_outage: bool


def summarize(
    samples: list[ProbeSample], *, pass_rate_threshold: float = PASS_RATE_THRESHOLD
) -> dict:
    """main側の成功率と機械判定・dense側の参考集計(純関数)。"""
    main = [s for s in samples if s.kind == "main"]
    dense = [s for s in samples if s.kind == "dense"]
    counted = [s for s in main if not s.tagged_planned_outage]
    ok = sum(1 for s in counted if s.ok)
    rate = (ok / len(counted)) if counted else None
    verdict = rate is not None and rate >= pass_rate_threshold

    max_streak = {"count": 0, "started_at": None, "ended_at": None}
    streak = 0
    for s in counted:
        if s.ok:
            streak = 0
            continue
        streak += 1
        if streak == 1:
            streak_started = s.at.isoformat()
        if streak > max_streak["count"]:
            max_streak = {
                "count": streak,
                "started_at": streak_started,
                "ended_at": s.at.isoformat(),
            }
    timeouts = sum(1 for s in counted if s.error == "timeout")
    errors: dict[str, int] = {}
    for s in counted:
        if not s.ok and s.error is not None:
            errors[s.error] = errors.get(s.error, 0) + 1
    return {
        "main_total": len(main),
        "main_excluded_planned_outage": len(main) - len(counted),
        "main_counted": len(counted),
        "main_ok": ok,
        "main_error": len(counted) - ok,
        "main_success_rate": rate,
        "verdict_995": verdict,
        "dense_total": len(dense),
        "dense_ok": sum(1 for s in dense if s.ok),
        "max_consecutive_failures": max_streak,
        "timeouts": timeouts,
        "errors_by_kind": dict(sorted(errors.items())),
    }


class TokenCache:
    """subject→access_tokenのキャッシュ(50分で再発行・client.py同型の交換)。

    鍵ファイルは初回_refresh時に読む(純ロジック試験がkeys_dir=Noneで
    サブクラス化できるように・CLIは起動直後のbearer()で即読み=fail-fast不変)。
    """

    def __init__(
        self,
        *,
        http: httpx.AsyncClient,
        keys_dir: str | Path,
        subject: str,
        clock: Clock,
    ) -> None:
        self._http = http
        self._keys_dir = keys_dir
        self._subject = subject
        self._clock = clock
        self._token: str | None = None
        self._issued_at: datetime | None = None

    async def bearer(self) -> str:
        now = self._clock.now()
        if (
            self._token is None
            or self._issued_at is None
            or (now - self._issued_at).total_seconds() >= TOKEN_REISSUE_SEC
        ):
            await self._refresh()
        return f"Bearer {self._token}"

    async def _refresh(self) -> None:
        private_key = (Path(self._keys_dir) / "idp_test_private.pem").read_bytes()
        idp_token = issue_idp_token(
            provider="google",
            subject=self._subject,
            private_key_pem=private_key,
            # 365日: サーバはexpを仮想Clock(ControlledClock)で検証するため・
            # 仮想時計が実時間より大きく先行した状態(月跨ぎClock操作後)での
            # プローブ実行でもIdPトークンが切れないようにする(supervisor検証fix)
            expires_in=365 * 24 * 3600,
        )
        r = await self._http.post(
            "/v1/auth/token", json={"provider": "google", "idp_token": idp_token}
        )
        r.raise_for_status()
        self._token = r.json()["access_token"]
        self._issued_at = self._clock.now()


async def run_probe(
    *,
    http: httpx.AsyncClient | None,
    base_url: str,
    token_cache: TokenCache | None,
    duration_s: float,
    clock: Clock,
    main_interval_s: float = MAIN_INTERVAL_S,
    dense_interval_s: float = DENSE_INTERVAL_S,
    timeout_s: float = REQUEST_TIMEOUT_S,
    marker: Path | None = DEFAULT_MARKER,
    request_path=None,
    bearer_factory=None,
    on_tick=None,
    sleep: Callable[[float], Awaitable[None]] = asyncio.sleep,
) -> list[ProbeSample]:
    """main/denseの2ループをduration_sまで回す。

    request_path/bearer_factory/on_tick/sleepはunit注入用(既定は実HTTPと
    TokenCache.bearerとasyncio.sleep・collector.pyと同一の注入作法)。
    マーカーは各サンプル記録時の存在確認で判定する(実行中タグ記入・
    §2.1読み替え2)。タイムアウトはerror="timeout"として集計(summarizeの
    timeouts/errors_by_kindと同一キー)。待機はasyncio.sleep(時刻参照では
    ない・stub.pyと同一規律)で・unitはsleep注入でClockを進めて決定的にする。
    """

    async def _request(path: str, bearer: str) -> tuple[bool, str | None]:
        if request_path is not None:
            # 注入側はasync(test_loadgen_probeのfake_requestと同一契約)
            await request_path(path, bearer)
            return True, None
        try:
            r = await http.get(
                path, headers={"Authorization": bearer}, timeout=timeout_s
            )
        except httpx.TimeoutException:
            return False, "timeout"
        except Exception as exc:  # 接続失敗等・プローブは止めない
            return False, type(exc).__name__
        return r.status_code == 200, (
            None if r.status_code == 200 else f"HTTP {r.status_code}"
        )

    async def _bearer() -> str:
        if bearer_factory is not None:
            return bearer_factory()
        return await token_cache.bearer()

    samples: list[ProbeSample] = []

    async def _once(kind: str, at: datetime, bearer: str) -> None:
        tagged = marker.exists() if marker is not None else False
        for path in PROBE_PATHS:
            ok, error = await _request(path, bearer)
            samples.append(
                ProbeSample(
                    kind=kind,
                    at=at,
                    path=path,
                    ok=ok,
                    error=error,
                    tagged_planned_outage=tagged,
                )
            )
        if on_tick is not None:
            on_tick()

    async def _loop(interval: float, kind: str) -> None:
        start = clock.now()
        while (clock.now() - start).total_seconds() < duration_s:
            now = clock.now()
            bearer = await _bearer()
            await _once(kind, now, bearer)
            await sleep(interval)

    await asyncio.gather(
        _loop(main_interval_s, "main"), _loop(dense_interval_s, "dense")
    )
    return samples


def parse_outages(raw: str) -> list[tuple[datetime, datetime]]:
    """事後集計用の窓指定パース("ISO:ISO,ISO:ISO")。§8手順では未使用
    (マーカー方式に一本化・§2.1読み替え2)だが、証拠の窓記録に残す。"""
    windows: list[tuple[datetime, datetime]] = []
    if not raw:
        return windows
    for part in raw.split(","):
        start_s, _, end_s = part.partition(":")
        start = datetime.fromisoformat(start_s)
        end = datetime.fromisoformat(end_s)
        if start.tzinfo is None or end.tzinfo is None:
            raise ValueError("タイムゾーン付きISO8601で指定する")
        windows.append((start, end))
    return windows
