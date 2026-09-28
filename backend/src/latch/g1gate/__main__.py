"""python -m latch.g1gate(design §2.7-A)。G1精度ゲートharnessのCLI。

実API経路はこのCLIのみ(make g1-gate)。pytest(make test/test-ci)には
実API呼び出しが構造的に存在しない(marker漏れの誤発火が原理的に起きない)。
起動時にllm_mode=realと鍵を検証し、不足なら即座に拒否する(誤実行防止)。
"""

from __future__ import annotations

import argparse
import asyncio
import sys
from pathlib import Path

from latch.core.clock import SystemClock
from latch.g1gate.cases import BASE_CURRENT_DATETIME, load_alcohol, load_parser_struct
from latch.g1gate.compare import (
    alcohol_reference_counts,
    summarize_alcohol,
    summarize_struct,
)
from latch.g1gate.report import build_report, sha256_file, sha256_text, write_report
from latch.g1gate.runner import build_gate_service, run_all
from latch.intents.prompt import PARSER_SYSTEM_PROMPT
from latch.llm.anthropic import ANTHROPIC_PARSER_MODEL
from latch.settings import Settings

DEFAULT_ASSETS = Path("../docs/testassets")  # make g1-gateはbackend/をcwdとして起動


def build_arg_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="latch.g1gate",
        description=("G1精度ゲートharness(Parser構造化精度+alcohol_involved・09 §4.3)"),
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
        help="各セット先頭N件のみ実行(部分実行・デバッグ用)",
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_arg_parser().parse_args(argv)
    settings = Settings()
    # 起動検証(design §2.7): stubのまま実測したと錯覚させない
    if settings.llm_mode != "real":
        print(
            "ERROR: LATCH_LLM_MODE=real が必要です"
            "(make g1-gate は .env を --env-file で読みます)",
            file=sys.stderr,
        )
        return 2
    if not settings.llm_anthropic_api_key:
        print(
            "ERROR: LATCH_ANTHROPIC_API_KEY が未設定です(.env へ設定してください)",
            file=sys.stderr,
        )
        return 2

    struct_set = load_parser_struct(args.assets / "g1-parser-struct.yaml")
    alcohol_set = load_alcohol(args.assets / "g1-alcohol.yaml")
    out_dir = args.out if args.out is not None else args.assets / "results"

    service = build_gate_service(settings)
    executed_at = SystemClock().now()  # 証拠の実施日(Clock経由)
    run = asyncio.run(
        run_all(
            service,
            struct_set,
            alcohol_set,
            limit=args.limit,
            echo=lambda message: print(message, file=sys.stderr),
        )
    )
    report = build_report(
        executed_at=executed_at,
        model=ANTHROPIC_PARSER_MODEL,
        llm_mode=settings.llm_mode,
        parser_prompt_sha256=sha256_text(PARSER_SYSTEM_PROMPT),
        input_sets={
            "parser_struct": {
                "file": "g1-parser-struct.yaml",
                "sha256": sha256_file(args.assets / "g1-parser-struct.yaml"),
            },
            "alcohol": {
                "file": "g1-alcohol.yaml",
                "sha256": sha256_file(args.assets / "g1-alcohol.yaml"),
            },
        },
        base_current_datetime=BASE_CURRENT_DATETIME.isoformat(),
        limit=args.limit,
        struct=summarize_struct(run.struct_outcomes),
        struct_cases=run.struct_outcomes,
        alcohol_reference=alcohol_reference_counts(run.struct_outcomes),
        alcohol=summarize_alcohol(run.alcohol_outcomes),
        alcohol_cases=run.alcohol_outcomes,
        error_results=run.error_outcomes,
        incomplete=run.incomplete,
    )
    path = write_report(report, out_dir=out_dir, executed_at=executed_at)
    print(f"report: {path}")
    print(
        f"overall_passed: {report['overall_passed']}"
        f" (incomplete={report['incomplete']})"
    )
    return 0 if report["overall_passed"] else 1


if __name__ == "__main__":
    sys.exit(main())
