"""python -m latch.g2gate(design §2.8)。G2日本語評価harnessのCLI。

実API経路はこのCLIのみ(make g2-gate)。pytest(make test/test-ci)には実API
呼び出しが構造的に存在しない(g1gateと同一規律)。起動時にllm_mode=realと
3鍵を検証し、不足なら即座に拒否する(誤実行防止)。
"""

from __future__ import annotations

import argparse
import asyncio
import sys
from pathlib import Path

from latch.core.clock import FakeClock, SystemClock
from latch.g2gate.cases import G2_BASE_CURRENT_DATETIME, load_goldset
from latch.g2gate.compare import compute_metrics
from latch.g2gate.report import build_report, questions_sha256, write_report
from latch.g2gate.runner import run_routes, usage_totals
from latch.llm.gateway import build_worker_gateway
from latch.settings import Settings

DEFAULT_ASSETS = Path("../docs/testassets")  # make g2-gateはbackend/をcwdとして起動


def build_arg_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="latch.g2gate",
        description="G2日本語評価harness(TypeSafe Jev+フォールバック両経路・09 §4)",
    )
    parser.add_argument(
        "--assets",
        type=Path,
        default=DEFAULT_ASSETS,
        help="入力セットディレクトリ(既定 %(default)s)",
    )
    parser.add_argument(
        "--out",
        type=Path,
        default=None,
        help="レポート出力ディレクトリ(既定 <assets>/results)",
    )
    parser.add_argument(
        "--limit",
        type=int,
        default=None,
        help="各route先頭Nペアのみ実行(部分実行・動作確認用。証拠外)",
    )
    parser.add_argument(
        "--route",
        choices=("first", "fallback", "both"),
        default="both",
        help="評価経路(既定 %(default)s)",
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_arg_parser().parse_args(argv)
    settings = Settings()
    if settings.llm_mode != "real":
        print(
            "ERROR: LATCH_LLM_MODE=real が必要です"
            "(make g2-gate は .env を --env-file で読みます)",
            file=sys.stderr,
        )
        return 2
    if not settings.llm_typesafe_api_key:
        print(
            "ERROR: LATCH_TYPESAFE_API_KEY が未設定です(.env へ設定してください)",
            file=sys.stderr,
        )
        return 2
    if not settings.llm_anthropic_api_key:
        print(
            "ERROR: LATCH_ANTHROPIC_API_KEY が未設定です(.env・フォールバック経路用)",
            file=sys.stderr,
        )
        return 2
    if not settings.llm_gemini_api_key:
        print(
            "ERROR: LATCH_GEMINI_API_KEY が未設定です"
            "(build_worker_gatewayのreal構成は3鍵必須)",
            file=sys.stderr,
        )
        return 2
    goldset = load_goldset(args.assets / "g2-jev-goldset.yaml")
    # 送信記録occurred_atの基準=goldset基準日時(FakeClock)。実施日時(証拠)は
    # SystemClock(arch test規律 — g1gate runnerと同一分担)
    gateway = build_worker_gateway(FakeClock(G2_BASE_CURRENT_DATETIME), settings)
    executed_at = SystemClock().now()
    outcomes = asyncio.run(
        run_routes(
            gateway,
            goldset,
            route=args.route,
            limit=args.limit,
            echo=lambda message: print(message, file=sys.stderr),
        )
    )
    routes = ("first", "fallback") if args.route == "both" else (args.route,)
    metrics_by_route = {
        rt: compute_metrics([o for o in outcomes if o.route == rt], goldset)
        for rt in routes
    }
    report = build_report(
        executed_at=executed_at,
        route=args.route,
        goldset_file="g2-jev-goldset.yaml",
        goldset_sha256=goldset.yaml_sha256,
        questions_sha=questions_sha256(),
        metrics_by_route=metrics_by_route,
        outcomes=outcomes,
        usage_totals=usage_totals(outcomes),
        limit=args.limit,
    )
    out_dir = args.out if args.out is not None else args.assets / "results"
    path = write_report(report, out_dir=out_dir, executed_at=executed_at)
    print(f"report: {path}")
    print(f"pairs: {len(outcomes)} partial={report['meta']['partial']}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
