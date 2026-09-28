"""G1精度ゲートharness(M1 ws-6)。CLI(make g1-gate)専用パッケージ。

pytest実行経路に実APIは存在しない(design §2.7-A)。公開IFの再exportのみ。
"""

from latch.g1gate.cases import (
    BASE_CURRENT_DATETIME,
    AlcoholSet,
    ParserStructSet,
    load_alcohol,
    load_parser_struct,
)
from latch.g1gate.compare import (
    ALCOHOL_PRECISION_MIN,
    ALCOHOL_RECALL_MIN,
    THRESHOLDS,
    summarize_alcohol,
    summarize_struct,
)
from latch.g1gate.report import build_report, write_report
from latch.g1gate.runner import GateRunResult, build_gate_service, run_all

__all__ = [
    "ALCOHOL_PRECISION_MIN",
    "ALCOHOL_RECALL_MIN",
    "BASE_CURRENT_DATETIME",
    "AlcoholSet",
    "GateRunResult",
    "ParserStructSet",
    "THRESHOLDS",
    "build_gate_service",
    "build_report",
    "load_alcohol",
    "load_parser_struct",
    "run_all",
    "summarize_alcohol",
    "summarize_struct",
    "write_report",
]
