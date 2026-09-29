"""embedding_completed 起点の第2段前半のオーケストレータ(design §2.4・§2.1案A)。

起点検証→Layer 2検索→Layer 3計算→match_candidates記録。Layer 3 は Layer 2 と
同一トランザクション・同一実行経路で組む(design §2.1案A): Layer 2 通過全件
(≤K_v=50)へ cheap_score を計算し全件の cheap_judge_score をUPSERTし、上位
K_c=20(降順・同点intent_id昇順)を topkc として次層(Layer 4・ws-5)へ返す。
トランザクションは呼び出し側(engine.begin() で包む — design §2.3)。例外は
握らず呼び出し側(stage1の再試行ループに載る経路)へ伝播させる。モジュール
属性経由で origin/layer2/candidates を呼ぶ(unit試験がmonkeypatchで差し替え
可能)。layer3 は純関数のため実物を呼ぶ(決定的)。
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass, field

from sqlalchemy.ext.asyncio import AsyncConnection

from latch.core.clock import Clock
from latch.worker.matching import candidates, layer2, layer3, origin
from latch.worker.matching.candidates import CandidatePair
from latch.worker.matching.layer3 import ScoredCandidate


@dataclass(frozen=True)
class RetrievalOutcome:
    """run_candidate_retrieval の結果(design §2.4 — 試験assertと
    将来トレースの供給源)。topkc は Layer 4(ws-5)への出力。"""

    intent_id: uuid.UUID
    version: int | None  # no-op時はNone
    layer1_pass_count: int
    pairs: list[CandidatePair] = field(default_factory=list)
    topkc: list[ScoredCandidate] = field(default_factory=list)
    skip_reason: str | None = None


async def run_candidate_retrieval(
    conn: AsyncConnection, clock: Clock, intent_id: uuid.UUID
) -> RetrievalOutcome:
    """起点検証→Layer 2検索→Layer 3計算→UPSERT(design §2.1案A・§2.4)。"""
    loaded = await origin.load_origin(conn, clock, intent_id)
    if loaded.skip_reason is not None:
        return RetrievalOutcome(
            intent_id=intent_id,
            version=None,
            layer1_pass_count=0,
            skip_reason=loaded.skip_reason,
        )
    assert loaded.origin is not None  # skip_reason Noneならoriginがある
    org = loaded.origin
    rows, pass_count = await layer2.retrieve_topk(conn, org)
    pairs: list[CandidatePair] = []
    scored: list[ScoredCandidate] = []
    for c in rows:
        rule = layer3.rule_score(
            origin_time_start=org.time_start,
            cand_time_start=c.time_start,
            origin_budget=org.budget_max,
            cand_budget=c.budget_max,
        )
        vocab = layer3.vocab_overlap(org.soft_texts, c.soft_texts)
        cheap = layer3.cheap_score(c.similarity, rule, vocab)
        scored.append(
            ScoredCandidate(
                intent_id=c.intent_id,
                version=c.version,
                similarity=c.similarity,
                cheap_score=cheap,
            )
        )
        pairs.append(
            await candidates.upsert_candidate(
                conn,
                origin=org,
                candidate_id=c.intent_id,
                candidate_version=c.version,
                similarity=c.similarity,
                cheap_score=cheap,
            )
        )
    return RetrievalOutcome(
        intent_id=org.intent_id,
        version=org.version,
        layer1_pass_count=pass_count,
        pairs=pairs,
        topkc=layer3.select_top_kc(scored),
    )
