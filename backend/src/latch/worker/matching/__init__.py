"""第2段マッチング前半: Layer 1〜3(M2 ws-3 + ws-4)。

公開APIは AsyncConnection を第一引数に取る純関数群(design §2.4)。
Layer 3 まで実配線済み(stage1 matching_hook・M2 ws-4)。K_v・K_c・500円は
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
from latch.worker.matching.layer3 import (
    K_CHEAP,
    ScoredCandidate,
    cheap_score,
    rule_score,
    select_top_kc,
    soft_texts,
    vocab_overlap,
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
    "K_CHEAP",
    "K_VECTORS",
    "LAYER1_WHERE",
    "CandidatePair",
    "HardCandidate",
    "Origin",
    "OriginLoad",
    "RetrievedCandidate",
    "RetrievalOutcome",
    "ScoredCandidate",
    "bind_params",
    "cheap_score",
    "hard_filter_candidates",
    "load_origin",
    "normalize_pair",
    "retrieve_topk",
    "rule_score",
    "run_candidate_retrieval",
    "select_top_kc",
    "soft_texts",
    "upsert_candidate",
    "vocab_overlap",
]
