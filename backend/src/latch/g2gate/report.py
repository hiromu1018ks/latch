"""G2評価の証拠レポート書き出し(design §2.8・10 §5の証拠形式はg1と同一)。

docs/testassets/results/g2-jev-result-YYYYMMDD-HHMMSS.yaml(JST実行時刻)。
--limit部分実行時はレポート先頭に「部分実行=証拠外」を明記する。
合否判定は組み込まない(基準はG2実施前にオーナー確定 — design §1.4-1)。
参考値としてgoldset-plan §11手順2の「Precision 0.60以上@閾値0.80」を表示する。
"""

from __future__ import annotations

import hashlib
from datetime import datetime
from pathlib import Path

import yaml

from latch.core.clock import JST
from latch.g2gate.runner import PairOutcome

REFERENCE_THRESHOLD = 0.80  # goldset-plan §11手順2の参考値表示(判定はしない)
REFERENCE_PRECISION = 0.60

_PARTIAL_NOTE = "部分実行(--limit)=証拠外。G2判定には520全件実行のレポートを使うこと"


def sha256_text(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def questions_sha256() -> str:
    """JEV_QUESTIONS定数のSHA(design §2.8のmeta証跡・dict順安定化つき)。"""
    import json

    from latch.llm.jev import JEV_QUESTIONS

    return sha256_text(json.dumps(JEV_QUESTIONS, ensure_ascii=False, sort_keys=True))


def build_report(
    *,
    executed_at: datetime,
    route: str,
    goldset_file: str,
    goldset_sha256: str,
    questions_sha: str,
    metrics_by_route: dict[str, dict],
    outcomes: list[PairOutcome],
    usage_totals: dict[str, dict],
    limit: int | None,
) -> dict:
    partial = limit is not None
    report: dict = {}
    if partial:
        report["note"] = _PARTIAL_NOTE
    report["meta"] = {
        "trial_id": f"g2-{executed_at.astimezone(JST):%Y%m%d-%H%M%S}",
        "executed_at": executed_at.isoformat(),
        "route": route,
        "partial": partial,
        "limit": limit,
        "pair_count": len(outcomes),
        "goldset": {"file": goldset_file, "sha256": goldset_sha256},
        "jev_questions_sha256": questions_sha,
        "usage_totals": usage_totals,
        "reference": {
            "note": "参考値(合否判定はG2実施前にオーナーが確定 — 09 §4)",
            "precision_at": REFERENCE_THRESHOLD,
            "precision_min": REFERENCE_PRECISION,
        },
    }
    report["routes"] = metrics_by_route
    report["per_pair"] = [
        {
            "id": o.pair_id,
            "route": o.route,
            "error": o.error,
            "would_a_accept_b": (
                None if o.judgment is None else o.judgment.result["would_a_accept_b"]
            ),
            "would_b_accept_a": (
                None if o.judgment is None else o.judgment.result["would_b_accept_a"]
            ),
            "mutual_score": o.mutual_score,
        }
        for o in outcomes
    ]
    return report


def write_report(report: dict, *, out_dir: Path, executed_at: datetime) -> Path:
    out_dir.mkdir(parents=True, exist_ok=True)
    stamp = executed_at.astimezone(JST).strftime("%Y%m%d-%H%M%S")
    path = out_dir / f"g2-jev-result-{stamp}.yaml"
    path.write_text(
        yaml.safe_dump(report, sort_keys=False, allow_unicode=True),
        encoding="utf-8",
    )
    return path
