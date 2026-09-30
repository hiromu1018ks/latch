"""G2日本語評価harness(M2 ws-8)。CLI(make g2-gate)専用パッケージ。

pytest実行経路に実APIは存在しない(design §2.8)。公開IFの再exportのみ。
"""

from latch.g2gate.cases import (
    G2_BASE_CURRENT_DATETIME,
    Goldset,
    load_goldset,
)
from latch.g2gate.compare import THRESHOLDS, compute_metrics
from latch.g2gate.report import build_report, write_report
from latch.g2gate.runner import PairOutcome, run_routes, usage_totals

__all__ = [
    "G2_BASE_CURRENT_DATETIME",
    "Goldset",
    "PairOutcome",
    "THRESHOLDS",
    "build_report",
    "compute_metrics",
    "load_goldset",
    "run_routes",
    "usage_totals",
    "write_report",
]
