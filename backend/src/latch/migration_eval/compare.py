"""移行評価: B0(改善なし)と I1〜I5 の比較harness(計画 §6 T11・§5.1)。

比較方法(§5.1):
- 改善なしの移植版をB0とし、同じデータ・Clock・モデル・prompt・閾値・
  候補枠で各施策を一つずつ比較する
- 単体の決定的試験では fake Clock と記録済みLLM結果(stub)を使い、
  外部呼出し回数を正確に数える
- 削減効果はB0との差で計算する
- 実環境(実Workers・実LLM・日本からの実測)は実配備の別承認後に行い、
  本harnessは同じ測定面を再生できるようにする

シナリオ(§5.1の追加入力を含む): 同時更新・失敗・日跨ぎ・高密度・
3〜4人グループを対象に含める。
"""

from __future__ import annotations

import asyncio
import time
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any

from latch.adapters.d1.database import LocalD1Database
from latch.adapters.d1.evaluation_cache import D1EvaluationCache
from latch.adapters.d1.evaluations import D1EvaluationGateway
from latch.domain.evaluation_key import EvaluationVersions, build_evaluation_key
from latch.domain.types import EvaluationKey
from latch.runtime.cloudflare.jobs import EvaluationJobRunner


@dataclass
class ImprovementFlags:
    """施策トグル(B0 = all False)。"""

    i1_claim_before_call: bool = False  # 処理前の確保
    i2_evaluation_cache: bool = False  # 評価cache
    i4_parse_aggregation: bool = False  # 解析集約(同一実行内)


@dataclass
class Measurement:
    label: str
    external_calls: int = 0
    wall_ms: float = 0.0
    db_batches: int = 0
    details: dict[str, Any] = field(default_factory=dict)


@dataclass
class ComparisonReport:
    baseline: str = "B0(改善なし移植版)"
    fixture: str = ""
    flags: dict[str, bool] = field(default_factory=dict)
    measurements: dict[str, Measurement] = field(default_factory=dict)
    condition_deltas: dict[str, Any] = field(default_factory=dict)
    unresolved: list[str] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)


class SameKeyEvaluationScenario:
    """同一評価キーの並行再評価(§5.2 I1・§5.3 I2の測定場)。

    B0: 並行20回すべてLLMを呼ぶ(呼出し20)
    I1: claim確保で1回だけ
    I1+I2: 直近の同fingerprint結果を再利用(同時刻の再評価は0回)
    """

    def __init__(
        self,
        db: LocalD1Database,
        flags: ImprovementFlags,
        *,
        versions: EvaluationVersions | None = None,
    ) -> None:
        self.db = db
        self.flags = flags
        self.versions = versions or EvaluationVersions(
            normalizer="n1",
            questions="q1",
            prompt="p1",
            schema="s1",
            provider="typesafe_jev",
            model="jev-1.13.0",
            policy="t0.60",
        )
        self.gateway = D1EvaluationGateway(db)
        self.cache = D1EvaluationCache(db)
        self.calls = 0

    async def _send_ok(self, key: EvaluationKey):
        await asyncio.sleep(0)  # 並行窓
        self.calls += 1
        return type(
            "SR",
            (),
            {
                "status": "succeeded",
                "actual_provider": self.versions.provider,
                "actual_model": self.versions.model,
                "input_tokens": 120,
                "output_tokens": 8,
                "cost_confirmed_micros": 1500,
                "error_kind": None,
                "sdk_internal_retry": 0,
                "payload": {"noul": 3.6},
            },
        )()

    async def run_parallel(self, key: EvaluationKey, *, workers: int = 20):
        """並行20評価。flagsに応じてclaim/cacheの前段を挟む。"""
        if self.flags.i1_claim_before_call:
            runner = EvaluationJobRunner(self.gateway)
            results = await asyncio.gather(
                *[
                    runner.run(
                        key,
                        owner=f"w{i}",
                        sender=self._send_ok,
                        requested_provider=self.versions.provider,
                        requested_model=self.versions.model,
                        price_version="2026-10",
                        now=datetime(2026, 10, 8, tzinfo=None)
                        if False
                        else _fixed_now(),
                    )
                    for i in range(workers)
                ]
            )
            return {
                "applied": sum(1 for r in results if r.get("completed")),
                "calls": self.calls,
            }
        # B0: 全員が呼ぶ(cacheだけ試す場合は先に見る)
        if self.flags.i2_evaluation_cache:
            hit = await self.cache.get_cached_evaluation(key, _fixed_now())
            if hit is not None:
                return {"applied": workers, "calls": 0, "cache_hit": True}
        results = await asyncio.gather(*[self._send_ok(key) for _ in range(workers)])
        stored = 0
        if self.flags.i2_evaluation_cache:
            ok, _ = await self.cache.store_evaluation(
                key,
                result={"noul": 3.6},
                actual_provider=self.versions.provider,
                actual_model=self.versions.model,
                requested_provider=self.versions.provider,
                requested_model=self.versions.model,
                intent_expires_at=[None, None],
                now=_fixed_now(),
            )
            stored = 1 if ok else 0
        return {"applied": len(results), "calls": self.calls, "stored": stored}


def _fixed_now():
    from datetime import UTC, datetime

    return datetime(2026, 10, 8, 12, 0, 0, tzinfo=UTC)


async def measure_scenario(
    db: LocalD1Database, label: str, flags: ImprovementFlags
) -> Measurement:
    """同一キー20並行評価を測定(label: B0/I1/I1+I2)。"""
    scenario = SameKeyEvaluationScenario(db, flags)
    key = build_evaluation_key(
        pair=("a" * 36, "b" * 36),
        sent_inputs=("input-a", "input-b"),
        versions=scenario.versions,
    )
    start = time.perf_counter()
    result = await scenario.run_parallel(key)
    wall_ms = (time.perf_counter() - start) * 1000
    return Measurement(
        label=label,
        external_calls=result["calls"],
        wall_ms=round(wall_ms, 2),
        details={"result": result},
    )


async def build_comparison_report(
    db: LocalD1Database, *, fixture: str = "same-key-20parallel"
) -> ComparisonReport:
    """B0 → I1 → I1+I2 の測定と削減効果(§5.1共通計測)。"""
    report = ComparisonReport(fixture=fixture)
    b0 = await measure_scenario(db, "B0", ImprovementFlags())
    report.measurements["B0"] = b0
    i1 = await measure_scenario(db, "I1", ImprovementFlags(i1_claim_before_call=True))
    report.measurements["I1"] = i1
    i1i2 = await measure_scenario(
        db,
        "I1+I2",
        ImprovementFlags(i1_claim_before_call=True, i2_evaluation_cache=True),
    )
    report.measurements["I1+I2"] = i1i2
    # 削減効果(B0との差 — §5.1: 効果を単純に足し合わせない)
    report.condition_deltas = {
        "I1_external_calls_avoided": b0.external_calls - i1.external_calls,
        "I2_additional_calls_avoided_vs_I1": i1.external_calls - i1i2.external_calls,
        "note": "I2は同fingerprint再評価時の追加削減(別々に数える)",
    }
    report.notes = [
        "測定はローカル決定的試験(fake Clock・stub LLM結果・外部呼出し数厳密)",
        "実環境(実Workers・日本からの実測・実LLM)は実配備の別承認後",
        "費用積算は costs.py が測定単位から行う(§5.1: SQL回数≠料金)",
    ]
    report.unresolved = [
        "実Workersでのcold/warm・burst・障害後回収の実測(承認待ち)",
        "実LLMでの品質比較(承認待ち・G1/G2資産を再利用)",
        "I6(Jev枠・閾値)は別実験(本比較に含めない — 計画§5.7)",
    ]
    report.flags = {
        "B0": False,
        "I1": True,
        "I1+I2": True,
    }
    return report
