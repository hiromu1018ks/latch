"""staging/loadの制御Clock操作CLI(M4 ws-1 design §2.2)。

python -m latch.core.clock_cli show — 制御状態(基準点)と仮想時刻を表示
python -m latch.core.clock_cli set <ISO8601> — 仮想時刻を移動する(後退可)
python -m latch.core.clock_cli advance <秒> — 仮想時刻を前進させる

宛先Redisは LATCH_REDIS_URL のenv上書きでstaging/load宛へ向ける(Makefileの
staging-clockターゲットが127.0.0.1:16379/26379を設定)。同期redisクライアント
を使用する(ホスト側からの単発操作・イベントループ不要)。実時間の表示は
SystemClock経由(arch test — 本モジュールでは実時間APIの直接呼び出しを書かない)。
"""

from __future__ import annotations

import argparse
from datetime import datetime, timedelta

from latch.core.clock import ControlledClock, SystemClock
from latch.settings import Settings


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="latch.core.clock_cli",
        description="共有制御Clock(latch:clock:control)の操作CLI(M4 ws-1)",
    )
    sub = parser.add_subparsers(dest="command", required=True)
    sub.add_parser("show", help="制御状態(基準点)と仮想時刻を表示")
    p_set = sub.add_parser("set", help="仮想時刻をISO8601へ移動する(後退可)")
    p_set.add_argument(
        "when",
        help="タイムゾーン付きISO8601(例: 2026-10-04T00:00:00+09:00)",
    )
    p_adv = sub.add_parser("advance", help="仮想時刻を<秒>だけ前進させる")
    p_adv.add_argument("seconds", type=float, help="前進秒(例: 86400 = 1日)")
    return parser


def _parse_iso(raw: str) -> datetime:
    """ISO8601をパースする(naiveは拒否 — JST意図のUTC解釈ずれを防ぐ)。"""
    dt = datetime.fromisoformat(raw)
    if dt.tzinfo is None:
        raise ValueError("タイムゾーン付きで指定する(例: 2026-10-04T00:00:00+09:00)")
    return dt


def _build_clock() -> ControlledClock:
    """LATCH_REDIS_URL(Settings・env上書き可)宛の同期クライアントで構築する。"""
    import redis as redis_sync

    settings = Settings()
    client = redis_sync.Redis.from_url(settings.redis_url, decode_responses=True)
    return ControlledClock(client)


def run_show(clock: ControlledClock) -> int:
    base = clock.read_base()
    print(f"[clock] key: {clock.DEFAULT_KEY}")
    if base is None:
        print("[clock] control: unset (real time)")
    else:
        print(f"[clock] control base: real={base[0]:.3f} virtual={base[1]:.3f}")
    print(f"[clock] virtual now: {clock.now().isoformat()}")
    print(f"[clock] real now:    {SystemClock().now().isoformat()}")
    return 0


def run_set(clock: ControlledClock, when: datetime) -> int:
    clock.set(when)
    print(f"[clock] set virtual time -> {clock.now().isoformat()}")
    return 0


def run_advance(clock: ControlledClock, seconds: float) -> int:
    clock.advance(timedelta(seconds=seconds))
    print(f"[clock] advanced by {float(seconds)}s -> {clock.now().isoformat()}")
    return 0


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    clock = _build_clock()
    if args.command == "show":
        return run_show(clock)
    if args.command == "set":
        try:
            when = _parse_iso(args.when)
        except ValueError as exc:
            print(f"[clock] error: {exc}")
            return 2
        return run_set(clock, when)
    if args.command == "advance":
        return run_advance(clock, args.seconds)
    raise AssertionError(f"unreachable: {args.command}")


if __name__ == "__main__":
    raise SystemExit(main())
