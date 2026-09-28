"""第2段マッチング前半: Layer 1 Hard Filter + Layer 2 Candidate Retrieval(M2 ws-3)。

公開APIは AsyncConnection を第一引数に取る純関数群(design §2.4)。
stage1(embedding_completed種別)への実配線は後続単位。K_v・500円は
module定数(design §2.8-3 — settings化しない)。
"""

from latch.worker.matching.candidates import (
    CandidatePair,
    normalize_pair,
    upsert_candidate,
)
from latch.worker.matching.layer1 import (
    LAYER1_WHERE,
    PAIR_BUDGET_MIN_YEN,
    HardCandidate,
    hard_filter_candidates,
)
from latch.worker.matching.layer2 import (
    K_VECTORS,
    RetrievedCandidate,
    retrieve_topk,
)
from latch.worker.matching.origin import (
    Origin,
    OriginLoad,
    bind_params,
    load_origin,
)
from latch.worker.matching.runner import RetrievalOutcome, run_candidate_retrieval

__all__ = [
    "PAIR_BUDGET_MIN_YEN",
    "K_VECTORS",
    "LAYER1_WHERE",
    "CandidatePair",
    "HardCandidate",
    "Origin",
    "OriginLoad",
    "RetrievedCandidate",
    "RetrievalOutcome",
    "bind_params",
    "hard_filter_candidates",
    "load_origin",
    "normalize_pair",
    "retrieve_topk",
    "run_candidate_retrieval",
    "upsert_candidate",
]
