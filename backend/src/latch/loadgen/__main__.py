"""負荷注入器CLI(M4 ws-6・design §3.1)。

python -m latch.loadgen users --count 1000      # 負荷用ユーザー事前作成
python -m latch.loadgen perf                    # 性能試験区間(3相+収束+証拠)
python -m latch.loadgen massload --stage 10000  # 母集合投入(base-datetime必須)
python -m latch.loadgen bruteforce --stage 10000 # 注入100件+収束+証拠

宛先の既定はload環境(base=28000系・dsn=25432)。ci・staging宛には使わない。
実施手順はdocs/plans/M4/ws-6-plan.md §8(makeラップあり)。実行時刻と収束
待ちはClock経由(C2)。
"""

from __future__ import annotations

import argparse
import asyncio
from datetime import datetime
from pathlib import Path

import httpx

from latch.core.clock import Clock, SystemClock
from latch.loadgen import collect, massload
from latch.loadgen.client import (
    DEFAULT_BASE_URL,
    DEFAULT_DSN,
    DEFAULT_KEYS_DIR,
    LOAD_USER_SUBJECT,
    LoadClient,
    parse_text,
    perf_intent_payload,
    user_subject,
)
from latch.loadgen.schedule import (
    SendPlan,
    burst_plan,
    constant_rate_plan,
    run_open_model,
)
from latch.testassets.generator import build_seed, write_yaml

SEED_DIR = Path("backend/data/loadgen")
SETTLE_STABLE_SEC = 30.0
SETTLE_TIMEOUT_SEC = 600.0


async def wait_settled(
    *, dsn: str, clock: Clock, timeout_s: float = SETTLE_TIMEOUT_SEC
) -> float:
    """event_queue_pending=0が30秒連続するまで待つ(戻り値=所要秒)。"""
    import asyncpg

    start = clock.now()
    conn = await asyncpg.connect(dsn)
    try:
        stable_since: datetime | None = None
        while (clock.now() - start).total_seconds() < timeout_s:
            pending = await conn.fetchval(
                "SELECT count(*) FROM match_events WHERE status='pending'"
            )
            if pending == 0:
                if stable_since is None:
                    stable_since = clock.now()
                elif (clock.now() - stable_since).total_seconds() >= SETTLE_STABLE_SEC:
                    return (clock.now() - start).total_seconds()
            else:
                stable_since = None
            await asyncio.sleep(5.0)
    finally:
        await conn.close()
    raise TimeoutError(f"queue not settled within {timeout_s}s")


def _perf_plans(args) -> list[SendPlan]:
    """perf区間の全予定列(§2.5の3相・相間に1秒の空白)。

    seqは全体で一意(ペイロード決定生成とユーザー選択の多様化に使う)。
    相B末尾のdraft_activateは・draft作成→同senderでPATCH active化の連鎖。
    """
    plans: list[SendPlan] = []
    plans.extend(
        constant_rate_plan(rate_per_sec=args.rate, count=args.parse_count, kind="parse")
    )
    offset = plans[-1].at + 1.0
    save = constant_rate_plan(
        rate_per_sec=args.rate, count=args.save_active, kind="create"
    )
    plans.extend(
        SendPlan(at=p.at + offset, kind=p.kind, seq=p.seq + args.parse_count)
        for p in save
    )
    offset = plans[-1].at + 1.0
    seq_base = args.parse_count + args.save_active
    plans.extend(
        SendPlan(at=offset + i * 0.2, kind="draft_activate", seq=seq_base + i)
        for i in range(args.save_draft)
    )
    offset = plans[-1].at + 1.0
    plans.extend(
        SendPlan(
            at=offset + p.at, kind="create", seq=seq_base + args.save_draft + p.seq
        )
        for p in burst_plan(count=args.burst, duration_s=1.0, kind="create")
    )
    return plans


async def cmd_users(args) -> int:
    client = LoadClient(base_url=args.base_url, keys_dir=args.keys)
    created = existing = 0
    async with httpx.AsyncClient(base_url=args.base_url, timeout=30.0) as http:
        for i in range(args.count):
            subject = LOAD_USER_SUBJECT.format(nnnn=i + 1)
            status = await client.create_user(
                http,
                subject,
                f"Load{i + 1:04d}",
                datetime(1990, 1, 1),
                "負荷試験用ユーザー",
            )
            if status == 201:
                created += 1
            else:
                existing += 1  # 409=既存(再実行べき等)
            await client.consent(http, subject)
    print(f"users ready: created={created} existing={existing} (consent済み)")
    return 0


async def cmd_perf(args) -> int:
    clock = SystemClock()
    client = LoadClient(base_url=args.base_url, keys_dir=args.keys)
    started_at = clock.now()
    plans = _perf_plans(args)
    async with httpx.AsyncClient(base_url=args.base_url, timeout=60.0) as http_client:

        async def sender(plan: SendPlan) -> None:
            subject = user_subject(plan.seq)
            if plan.kind == "parse":
                await client.parse_intent(http_client, subject, parse_text(plan.seq))
            elif plan.kind == "create":
                status, _ = await client.create_intent(
                    http_client,
                    subject,
                    perf_intent_payload(plan.seq, started_at=started_at),
                )
            elif plan.kind == "draft_activate":
                await client.draft_activate(
                    http_client,
                    subject,
                    perf_intent_payload(plan.seq, started_at=started_at),
                )
            else:
                raise ValueError(f"unknown kind: {plan.kind}")

        stats = await run_open_model(plans, sender=sender, clock=clock)
    print(
        f"injection done: planned={stats.planned} ok={stats.ok} "
        f"error={stats.error} client_p95_ms={stats.client_p95_ms:.1f}"
    )
    settle = await wait_settled(dsn=args.dsn, clock=clock)
    print(f"queue settled in {settle:.0f}s")
    report = await collect.collect_perf_report(
        window=args.window,
        stats_summary={
            "planned": stats.planned,
            "ok": stats.ok,
            "error": stats.error,
            "errors": stats.errors[:10],
            "client_p95_ms": round(stats.client_p95_ms, 1),
        },
        env_note_data=collect.env_note(),
        clock=clock,
    )
    path = collect.write_evidence(report, prefix="ws6-load", clock=clock)
    print(f"evidence: {path}")
    return 0


async def cmd_massload(args) -> int:
    base = datetime.fromisoformat(args.base_datetime)
    if base.tzinfo is None:
        raise SystemExit("--base-datetime はタイムゾーン付きISO8601で指定してください")
    clock = SystemClock()
    seed = build_seed(base, count=args.stage)
    SEED_DIR.mkdir(parents=True, exist_ok=True)
    out = SEED_DIR / f"loadgen-seed-{args.stage}.yaml"
    write_yaml(seed, str(out))  # 手順の再現性のためgit管理外へ書き出し(§2.2(c))
    result = await massload.load_population(dsn=args.dsn, seed=seed, now=clock.now())
    print(f"massload: {result} (seed yaml: {out})")
    return 0


async def cmd_bruteforce(args) -> int:
    clock = SystemClock()
    client = LoadClient(base_url=args.base_url, keys_dir=args.keys)
    started_at = clock.now()
    plans = burst_plan(count=args.injected, duration_s=1.0, kind="create")
    async with httpx.AsyncClient(base_url=args.base_url, timeout=60.0) as http_client:

        async def sender(plan: SendPlan) -> None:
            subject = LOAD_USER_SUBJECT.format(
                nnnn=args.first_user + (plan.seq % args.users)
            )
            payload = perf_intent_payload(
                args.seq_offset + plan.seq, started_at=started_at
            )
            status, _ = await client.create_intent(http_client, subject, payload)
            if status != 201:
                raise RuntimeError(f"bf create {subject}: {status}")

        stats = await run_open_model(plans, sender=sender, clock=clock)
    print(f"bf injection done: ok={stats.ok} error={stats.error}")
    settle = await wait_settled(dsn=args.dsn, clock=clock)
    print(f"queue settled in {settle:.0f}s")
    report = await collect.collect_bruteforce_report(
        stage=args.stage,
        window=args.window,
        injected_events=args.injected,
        env_note_data=collect.env_note(),
        clock=clock,
    )
    path = collect.write_evidence(
        report, prefix=f"ws6-bruteforce-{args.stage}", clock=clock
    )
    print(f"evidence: {path}")
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="負荷注入器(M4 ws-6)")
    parser.add_argument("--base-url", default=DEFAULT_BASE_URL)
    parser.add_argument("--keys", default=DEFAULT_KEYS_DIR)
    parser.add_argument("--dsn", default=DEFAULT_DSN)
    sub = parser.add_subparsers(dest="command", required=True)

    p_users = sub.add_parser("users", help="負荷用ユーザー事前作成(users+consent)")
    p_users.add_argument("--count", type=int, default=1000)
    p_users.set_defaults(func=lambda a: asyncio.run(cmd_users(a)))

    p_perf = sub.add_parser("perf", help="性能試験区間(3相+収束待ち+証拠YAML)")
    p_perf.add_argument("--rate", type=float, default=50.0)
    p_perf.add_argument("--parse-count", type=int, default=3000)
    p_perf.add_argument("--save-active", type=int, default=2980)
    p_perf.add_argument("--save-draft", type=int, default=10)
    p_perf.add_argument("--burst", type=int, default=100)
    p_perf.add_argument("--window", default="15m")
    p_perf.set_defaults(func=lambda a: asyncio.run(cmd_perf(a)))

    p_ml = sub.add_parser("massload", help="総当たり母集合投入(Event発行なし)")
    p_ml.add_argument("--stage", type=int, required=True)
    p_ml.add_argument(
        "--base-datetime",
        required=True,
        help="タイムゾーン付きISO8601(全段階で同一値を使う=段階比較の公平性)",
    )
    p_ml.set_defaults(func=lambda a: asyncio.run(cmd_massload(a)))

    p_bf = sub.add_parser("bruteforce", help="総当たり注入100件+収束待ち+証拠YAML")
    p_bf.add_argument("--stage", type=int, default=10000)
    p_bf.add_argument("--injected", type=int, default=100)
    p_bf.add_argument(
        "--first-user",
        type=int,
        default=1,
        help="注入ユーザーの開始番号(段階間で101→201と変える・Active 5件余裕)",
    )
    p_bf.add_argument("--users", type=int, default=100, help="注入ユーザー数(1人1件)")
    p_bf.add_argument(
        "--seq-offset", type=int, default=11000, help="payload多様化のseq基点"
    )
    p_bf.add_argument("--window", default="30m")
    p_bf.set_defaults(func=lambda a: asyncio.run(cmd_bruteforce(a)))

    args = parser.parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    raise SystemExit(main())
