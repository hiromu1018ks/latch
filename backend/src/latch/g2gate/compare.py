"""G2評価の指標算出(09 §4〜§4.2・goldset-plan §11手順4〜5)。すべて純関数。

- MutualScore = min(would_a_accept_b, would_b_accept_a)・L = H×MutualScore×C
  (H=1・C=1 — layer1_pass=falseの30件はJev単体検証のためH=1で通す・§2.8)
- Precision/Recall: 提案=L≥閾値(0.70/0.80/0.90)・実YES=gold_mutual
- ECE: MutualScoreを10分割ビン([0,0.1)…[0.9,1])・各ビン|平均予測−実YES率|の
  加重平均(空ビンは重み0で自然に消える)
- Brier: (L − gold)^2 の平均
- Mutual Acceptance Precision: gold true群とfalse群のMutualScore分布の分離度
  — Mann-Whitney Uに基づくAUC(09 §4.2「ランク指標」の実装解釈・supervisor採用)
- 診断(参考値・正式な集計方法はG2実施時に確定 — goldset-plan §5):
  would_*のband(low/mid/high)一致率・latent_yes band一致率・
  fit軸0〜4の±1以内一致率(実測は正規化値のため×4で復元)
失敗ペアは指標の分母から除外せず失敗数で報告する(design §2.8)。
"""

from __future__ import annotations

THRESHOLDS = (0.50, 0.60, 0.70)  # 09 §4.2・D-01の3点(v0.6で運用閾値0.60を中心へ再設定)
ECE_BINS = 10
BAND_LOW, BAND_HIGH = 0.35, 0.65  # goldset-plan §5(low<0.35/mid/high≥0.65)
SCORE_AXIS_KEYS = ("purpose_fit", "mood_fit", "timing_fit", "social_fit")


def band_of(value: float) -> str:
    if value < BAND_LOW:
        return "low"
    if value < BAND_HIGH:
        return "mid"
    return "high"


def _mean(values: list[float]) -> float:
    return sum(values) / len(values)


def precision_recall(
    scores: list[float], golds: list[bool], threshold: float
) -> tuple[float | None, float | None]:
    """提案=L≥閾値・実YES=gold。提案0件/gold true 0件はNone(レポートはnull)。"""
    tp = sum(1 for s, g in zip(scores, golds, strict=True) if s >= threshold and g)
    proposed = sum(1 for s in scores if s >= threshold)
    precision = tp / proposed if proposed else None
    total_true = sum(1 for g in golds if g)
    recall = tp / total_true if total_true else None
    return precision, recall


def expected_calibration_error(
    scores: list[float], golds: list[bool], *, bins: int = ECE_BINS
) -> float | None:
    """10分割ビンECE。最終ビンは1.0を含む閉区間(§9-13)。"""
    n = len(scores)
    if n == 0:
        return None
    total = 0.0
    for i in range(bins):
        lo = i / bins
        hi = (i + 1) / bins
        idx = [
            k
            for k, s in enumerate(scores)
            if (lo <= s < hi) or (i == bins - 1 and s == 1.0)
        ]
        if not idx:
            continue
        avg = _mean([scores[k] for k in idx])
        obs = _mean([1.0 if golds[k] else 0.0 for k in idx])
        total += len(idx) / n * abs(avg - obs)
    return total


def brier_score(scores: list[float], golds: list[bool]) -> float | None:
    if not scores:
        return None
    return _mean(
        [(s - (1.0 if g else 0.0)) ** 2 for s, g in zip(scores, golds, strict=True)]
    )


def _rankdata(values: list[float]) -> list[float]:
    """1-indexed順位。タイは平均順位(Mann-Whitney Uの標準形)。"""
    order = sorted(range(len(values)), key=lambda i: values[i])
    ranks = [0.0] * len(values)
    i = 0
    while i < len(order):
        j = i
        while j + 1 < len(order) and values[order[j + 1]] == values[order[i]]:
            j += 1
        avg_rank = (i + j) / 2 + 1
        for k in range(i, j + 1):
            ranks[order[k]] = avg_rank
        i = j + 1
    return ranks


def auc_separation(pos: list[float], neg: list[float]) -> float | None:
    """gold true群(pos)がfalse群(neg)より上位に置けた率(U統計量ベースAUC)。"""
    if not pos or not neg:
        return None
    ranks = _rankdata(pos + neg)
    r_pos = sum(ranks[: len(pos)])
    u = r_pos - len(pos) * (len(pos) + 1) / 2
    return u / (len(pos) * len(neg))


def compute_metrics(outcomes, goldset) -> dict:
    """PairOutcome群+goldset期待値→指標dict。outcomesは単一routeのリスト。"""
    expected = goldset.expected
    success = [o for o in outcomes if o.error is None and o.judgment is not None]
    failed = [o for o in outcomes if o.error is not None]
    scores = [o.mutual_score for o in success]
    golds = [expected[o.pair_id].gold_mutual for o in success]
    pos = [s for s, g in zip(scores, golds, strict=True) if g]
    neg = [s for s, g in zip(scores, golds, strict=True) if not g]
    thresholds: dict[str, dict] = {}
    for t in THRESHOLDS:
        precision, recall = precision_recall(scores, golds, t)
        thresholds[f"{t:.1f}"] = {"precision": precision, "recall": recall}
    # 診断(参考値)
    band_match = {"would_a": [], "would_b": [], "latent_yes": []}
    fit_within_1: dict[str, list[bool]] = {k: [] for k in SCORE_AXIS_KEYS}
    for o in success:
        exp = expected[o.pair_id]
        result = o.judgment.result
        band_match["would_a"].append(
            band_of(result["would_a_accept_b"]) == exp.would_a_accept_b.band
        )
        band_match["would_b"].append(
            band_of(result["would_b_accept_a"]) == exp.would_b_accept_a.band
        )
        band_match["latent_yes"].append(
            band_of(result["jev_5axis"]["latent_yes"]["value"]) == exp.latent_yes.band
        )
        for key in SCORE_AXIS_KEYS:
            actual = result["jev_5axis"][key]["value"] * 4  # 正規化を0〜4へ復元
            fit_within_1[key].append(abs(actual - getattr(exp, key)) <= 1.0)
    diagnostics = {
        "would_a_band_match_rate": (
            _mean([1.0 if x else 0.0 for x in band_match["would_a"]])
            if success
            else None
        ),
        "would_b_band_match_rate": (
            _mean([1.0 if x else 0.0 for x in band_match["would_b"]])
            if success
            else None
        ),
        "latent_band_match_rate": (
            _mean([1.0 if x else 0.0 for x in band_match["latent_yes"]])
            if success
            else None
        ),
        "fit_within_1_rate": {
            k: (_mean([1.0 if x else 0.0 for x in v]) if v else None)
            for k, v in fit_within_1.items()
        },
    }
    failures: dict[str, int] = {}
    for o in failed:
        failures[o.error] = failures.get(o.error, 0) + 1
    return {
        "pair_count": len(outcomes),
        "success_count": len(success),
        "failure_count": len(failed),
        "failures": failures,
        "thresholds": thresholds,
        "ece": expected_calibration_error(scores, golds),
        "brier": brier_score(scores, golds),
        "auc": auc_separation(pos, neg),
        "diagnostics": diagnostics,
    }
