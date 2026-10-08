"""Vectorize adapter(T9・計画 §3.3・§6 T9)。

採否判断の前提(計画):
- 「初期の決定的ケースは正確検索と一致を要求する。近似検索を許す変更は
  品質許容差の承認後に評価する」
- VectorizeはANN(近似的最近傍)。同点順位(intent_id昇順)や高密度の
  同点帯での完全一致は保証できない
- ローカル(wrangler dev)にVectorize bindingのシミュレータは無く、
  実indexでの比較は実配備の別承認が必要

このため本adapterは「実indexがあるときに同一interfaceで比較できる」
実装のみを提供し、bindingが無い環境では正確検索(retrieval.py)へ
フォールバックする。採否は docs/testassets/results/
cloudflare-vectorize-decision.yaml に記録したとおり見送り(再判断条件付き)。
"""

from __future__ import annotations

from typing import Any

from latch.adapters.d1.database import LocalD1Database
from latch.adapters.d1.retrieval import (
    D1RetrievalRepository,
    RetrievedCandidate,
    RetrievalResult,
)
from latch.domain.types import IntentSnapshot

VECTORIZE_DIMENSIONS = 768  # 既存Embedding(gemini-embedding-001)と同一


def vector_id(intent_id: str, content_generation: str) -> str:
    """vectorのID = Intent ID + 内容の世代(§T9 Interfaces)。

    内容が変わったIntentは別IDで登録し直す(旧vectorを混ぜない)。
    """
    return f"{intent_id}:{content_generation}"


def parse_vector_id(vid: str) -> tuple[str, str]:
    intent_id, _, generation = vid.rpartition(":")
    return intent_id, generation


class VectorizeRetrievalRepository:
    """retrieve_vectorized — 正確検索と同じRetrievalResultを返す。

    index(binding)が無い環境では正確検索へ delegate する。
    実index比較は承認後に有効化する(COMPARISON_READY=False の間は
    決定的比較が正確検索と一致することだけを試験する)。
    """

    COMPARISON_READY = False  # 実Vectorize index比較の実施可否

    def __init__(
        self,
        db: LocalD1Database,
        exact: D1RetrievalRepository,
        index: Any = None,
    ) -> None:
        self.db = db
        self.exact = exact
        self.index = index  # 実binding(env.VECTORIZE)。None=フォールバック

    async def retrieve_vectorized(
        self, origin: IntentSnapshot, snapshot_id: str
    ) -> RetrievalResult:
        """同一interfaceの検索。index未接続なら正確検索と同一結果。"""
        if self.index is None:
            return await self.exact.retrieve_exact(origin, snapshot_id)
        return await self._query_index(origin, snapshot_id)

    async def _query_index(
        self, origin: IntentSnapshot, snapshot_id: str
    ) -> RetrievalResult:
        """実index問合せ(承認後の比較試験で使う・要実binding)。

        - metadata filterだけで表せる条件: category・status・同意
        - 残る厳密条件(Workers側で再判定): 時間交差・距離・予算・block・年齢
        - 検索上限: top_k=K_VECTORS×2(追加探索)→ 厳密条件で絞って上位50
        """
        raise NotImplementedError(
            "実Vectorize indexでの比較は実配備の別承認後に行う"
            "(計画§6 T9・採否記録 cloudflare-vectorize-decision.yaml)"
        )
