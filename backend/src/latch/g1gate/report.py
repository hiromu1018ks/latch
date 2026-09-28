"""G1ゲート実行結果のレポートYAML書き出し(design §2.10・10 §5の証拠)。

meta(試験ID・実施日・環境・入力条件・合否)とケース別詳細(actual=Parser出力
全体を含む — テスト入力は生産データでなく機微制限対象外・09 §4.1・design §6-7)。
"""

from __future__ import annotations

import hashlib
from dataclasses import asdict
from datetime import datetime
from pathlib import Path

import yaml

from latch.core.clock import JST
from latch.g1gate.compare import (
    AlcoholCaseOutcome,
    AlcoholSummary,
    ErrorCaseOutcome,
    StructCaseOutcome,
    StructSummary,
)


def sha256_text(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def sha256_file(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def build_report(
    *,
    executed_at: datetime,
    model: str,
    llm_mode: str,
    parser_prompt_sha256: str,
    input_sets: dict[str, dict],
    base_current_datetime: str,
    limit: int | None,
    struct: StructSummary,
    struct_cases: list[StructCaseOutcome],
    alcohol_reference: dict,
    alcohol: AlcoholSummary,
    alcohol_cases: list[AlcoholCaseOutcome],
    error_results: list[ErrorCaseOutcome],
    incomplete: bool,
) -> dict:
    """証拠レポートのdict構築(10 §5: 試験ID・実施日・環境・入力条件・合否)。"""
    overall = (
        struct.passed
        and alcohol.passed
        and not incomplete
        and all(e.ok for e in error_results)
    )
    return {
        "meta": {
            "trial_id": f"g1-{executed_at.astimezone(JST):%Y%m%d-%H%M%S}",
            "executed_at": executed_at.isoformat(),
            "llm_mode": llm_mode,
            "model": model,
            "parser_prompt_sha256": parser_prompt_sha256,
            "input_sets": input_sets,
            "base_current_datetime": base_current_datetime,
            "limit": limit,
        },
        "parser_struct": {
            "fields": {k: asdict(v) for k, v in struct.fields.items()},
            "passed": struct.passed,
            "cases": [
                {
                    "id": o.id,
                    "fields": {k: asdict(f) for k, f in o.fields.items()},
                    "alcohol_involved": asdict(o.alcohol_involved),
                    "actual": o.actual,
                    "error": o.error,
                }
                for o in struct_cases
            ],
        },
        "alcohol_reference": alcohol_reference,
        "alcohol": {
            **asdict(alcohol),
            "cases": [
                {
                    "id": o.id,
                    "expected": o.expected,
                    "classification": o.classification,
                    "actual": o.actual,
                    "actual_raw": o.actual_raw,
                    "error": o.error,
                }
                for o in alcohol_cases
            ],
        },
        "error_cases": [asdict(e) for e in error_results],
        "incomplete": incomplete,
        "overall_passed": overall,
    }


def write_report(report: dict, *, out_dir: Path, executed_at: datetime) -> Path:
    """g1-result-YYYYMMDD-HHMMSS.yaml(JST実行時刻)をout_dirへ書き出す。"""
    out_dir.mkdir(parents=True, exist_ok=True)
    stamp = executed_at.astimezone(JST).strftime("%Y%m%d-%H%M%S")
    path = out_dir / f"g1-result-{stamp}.yaml"
    path.write_text(
        yaml.safe_dump(report, sort_keys=False, allow_unicode=True),
        encoding="utf-8",
    )
    return path
