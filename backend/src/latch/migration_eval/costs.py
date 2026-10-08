"""移行評価: 費用見積り(計画 §6 T11・§8)。

規律(§8):
- Workers Paid最低$5を出発点に、実測利用量と各サービスの枠・超過単価
  から計算する
- 「SQL回数が減った=同じ割合で料金が減った」とは扱わない(D1は読取/
  書込行課金)
- 無料枠がアカウントの他用途と共有されるかは要確認(unresolved)
- token・試行・query/CPU・保存量を測定し、税・為替・一時二重稼働を
  含めて再計算する(本実装はUSD建て・円換算は報告時)

単価は2026-10時点の公開料金(出典は計画§8の各リンク)。
実測値との突合(実請求)は実配備後。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

# Workers Paid(月$5・2026-10時点の公開値)
WORKERS_PAID_MONTHLY_USD = 5.00
WORKERS_INCLUDED_REQUESTS = 10_000_000
WORKERS_INCLUDED_CPU_MS = 30_000_000
WORKERS_EXTRA_PER_MILLION_REQ_USD = 0.30
WORKERS_EXTRA_CPU_MS_PER_MILLION_USD = 0.02

# D1(Workers Paidに付属枠・超過単価)
D1_INCLUDED_ROWS_READ = 25_000_000_000
D1_INCLUDED_ROWS_WRITTEN = 50_000_000
D1_EXTRA_PER_MILLION_READ_USD = 0.001
D1_EXTRA_PER_MILLION_WRITE_USD = 1.00
D1_STORAGE_INCLUDED_GB = 5
D1_STORAGE_PER_GB_DAY_USD = 0.75 / 30  # $0.75/GB-月相当

# Queues(Operations課金)
QUEUES_INCLUDED_OPS = 10_000_000
QUEUES_EXTRA_PER_MILLION_OPS_USD = 0.40

# R2(暗号化export用・保存のみ想定)
R2_STORAGE_PER_GB_MONTH_USD = 0.015
R2_CLASS_A_PER_MILLION_USD = 4.50
R2_CLASS_B_PER_MILLION_USD = 0.36

# 外部LLM(実測単価はllm_attempts.cost_confirmed_microsの積算を使う)
LLM_ESTIMATED_PER_CALL_USD = 0.0015  # 暫定(Jev 1試行分の概算・実測で置換)


@dataclass
class UsageUnits:
    """1ヶ月の利用単位(測定値またはシナリオ見積)。"""

    requests: int = 0
    cpu_ms: int = 0
    d1_rows_read: int = 0
    d1_rows_written: int = 0
    d1_storage_gb: float = 0.0
    queue_ops: int = 0
    r2_storage_gb: float = 0.0
    r2_class_a: int = 0
    r2_class_b: int = 0
    llm_calls: int = 0
    llm_cost_usd: float = 0.0  # 実測積算(無い場合はllm_calls×暫定単価)


@dataclass
class CostBreakdown:
    workers: float = 0.0
    d1: float = 0.0
    queues: float = 0.0
    r2: float = 0.0
    llm: float = 0.0
    total: float = 0.0
    details: dict[str, Any] = field(default_factory=dict)


def _extra(used: int, included: int, per_million: float) -> float:
    return max(0, used - included) / 1_000_000 * per_million


def compute_monthly_cost(units: UsageUnits) -> CostBreakdown:
    """月額USD(Workers Paid前提・超過分は従量)。"""
    workers = WORKERS_PAID_MONTHLY_USD
    workers += _extra(
        units.requests, WORKERS_INCLUDED_REQUESTS, WORKERS_EXTRA_PER_MILLION_REQ_USD
    )
    workers += _extra(
        units.cpu_ms, WORKERS_INCLUDED_CPU_MS, WORKERS_EXTRA_CPU_MS_PER_MILLION_USD
    )
    d1 = _extra(
        units.d1_rows_read, D1_INCLUDED_ROWS_READ, D1_EXTRA_PER_MILLION_READ_USD
    )
    d1 += _extra(
        units.d1_rows_written,
        D1_INCLUDED_ROWS_WRITTEN,
        D1_EXTRA_PER_MILLION_WRITE_USD,
    )
    storage_gb = max(0.0, units.d1_storage_gb - D1_STORAGE_INCLUDED_GB)
    d1 += storage_gb * D1_STORAGE_PER_GB_DAY_USD * 30
    queues = _extra(
        units.queue_ops, QUEUES_INCLUDED_OPS, QUEUES_EXTRA_PER_MILLION_OPS_USD
    )
    r2 = units.r2_storage_gb * R2_STORAGE_PER_GB_MONTH_USD
    r2 += units.r2_class_a / 1_000_000 * R2_CLASS_A_PER_MILLION_USD
    r2 += units.r2_class_b / 1_000_000 * R2_CLASS_B_PER_MILLION_USD
    llm = units.llm_cost_usd or (units.llm_calls * LLM_ESTIMATED_PER_CALL_USD)
    return CostBreakdown(
        workers=round(workers, 4),
        d1=round(d1, 4),
        queues=round(queues, 4),
        r2=round(r2, 4),
        llm=round(llm, 4),
        total=round(workers + d1 + queues + r2 + llm, 4),
        details={
            "basis": "Workers Paid + 従量(D1/Queues/R2/LLM)",
            "included_assumption": "無料枠・付属枠が他用途と共有されない前提",
            "llm_basis": ("measured" if units.llm_cost_usd else "estimated_per_call"),
        },
    )


def estimate_units_from_measured(
    *,
    measured_calls_per_month: int,
    requests_multiplier: int = 6,  # API/画面/静的配信の倍率概算
    rows_read_per_eval: int = 400,  # D1 snapshot+検索読取(実測で更新)
    rows_written_per_eval: int = 30,
    queue_ops_per_eval: int = 4,
    active_intents_per_month: int = 3000,
    storage_gb: float = 0.2,
) -> UsageUnits:
    """測定(決定的試験)から月次単位を概算(§8: 見積は実測で置換する)。"""
    evals = active_intents_per_month
    return UsageUnits(
        requests=measured_calls_per_month * requests_multiplier,
        cpu_ms=evals * 50,
        d1_rows_read=evals * rows_read_per_eval,
        d1_rows_written=evals * rows_written_per_eval,
        d1_storage_gb=storage_gb,
        queue_ops=evals * queue_ops_per_eval,
        r2_storage_gb=0.5,
        r2_class_a=4,
        r2_class_b=30,
        llm_calls=measured_calls_per_month,
    )
