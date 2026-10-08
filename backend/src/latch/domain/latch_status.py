"""回答集計の純計算(T2分離・latches.service.compute_new_status と同一)。

yes→全員yesでmatched・未満でpartial_accept(必要人数=|S|・D-06)。
no/defer→即rejected(グループでも部分成立なし)。
PostgreSQL版とWorkers版の双方がこの同一実装を使う。
"""

from __future__ import annotations

RESPONSE_YES = "yes"


def compute_new_status(response: str, responses: list[dict], total: int) -> str:
    """回答後のlatch状態(design §2.4手順3)。"""
    if response != RESPONSE_YES:
        return "rejected"
    yes = sum(1 for r in responses if r.get("response") == RESPONSE_YES)
    return "matched" if yes == total else "partial_accept"
