"""Layer 5の純関数群・定数(06 §6・§10・03 D-05/D-07/D-08・design §2.4〜2.6)。

DB・async・SQLを持たない純計算のみ(unit試験は決定的)。jst_day_start は
layer4からimportして再利用(再実装しない — C2のJST導出を一元化)。
"""

from __future__ import annotations

from datetime import datetime, timedelta

from latch.core.clock import JST
from latch.worker.matching.layer4 import (  # noqa: F401 — 再export・試験が同一規律を検証
    jst_day_start,
)

# 06 §6「L >= 0.60」(D-01・v0.6)。0.80→0.60の変更根拠はG2日本語評価実測
# (docs/reviews/g2-threshold-fallback-revision.md・2026-09-30改版)
LATCH_THRESHOLD = 0.60
LATCH_C = 1.0  # 01 §14・C初期値(Calibration調整点・コード定数)
D07_DELTA = 0.05  # 06 §10(スコア変化判定)
D08_DAILY_LIMIT = 6  # 03 D-08(1ユーザー日次)
D08_CONCURRENT_LIMIT = 3  # 03 D-08(1Intent同時進行)
MATCH_LEVEL_HIGH_MIN = 0.90  # 05 §2(high/medium境界)

PROMPT_LEAD_MIN = timedelta(minutes=75)  # 06 §10(75分ルール)
DEADLINE_MIN_AFTER_NOTIFY = timedelta(minutes=15)  # 03 D-05
DEADLINE_MAX_AFTER_NOTIFY = timedelta(hours=2)  # 03 D-05
DEADLINE_BEFORE_START = timedelta(minutes=60)  # 03 D-05
DEFER_SUPPRESS_MAX = timedelta(hours=24)  # 03 D-07(min(24時間, 残時間/2)の上限)
BUCKET_MINUTES = 30  # 06 §9(30分Bucket)

RESPONSE_NO = "no"
RESPONSE_DEFER = "defer"


def response_deadline(
    now: datetime, target_time: datetime, min_expires_at: datetime
) -> datetime:
    """D-05回答期限式(03 D-05・design §2.6)。

    回答期限 = min( max(通知時刻+15分, min(通知時刻+2時間, 対象開始時刻−60分)),
    参加Intentのexpires_atの最小値 )。丸めなし。
    """
    inner = max(
        now + DEADLINE_MIN_AFTER_NOTIFY,
        min(now + DEADLINE_MAX_AFTER_NOTIFY, target_time - DEADLINE_BEFORE_START),
    )
    return min(inner, min_expires_at)


MATCH_LEVEL_MEDIUM_MIN = (
    0.80  # 05 §2: high>=0.90 / medium>=0.80 / low=提案閾値以上0.80未満
)
# v0.6注記: medium境界の0.80は表示用の区切りであり提案閾値(LATCH_THRESHOLD)と独立。
# 閾値0.80だった当時は偶然一致していたため分離した(2026-09-30改版)


def match_level(score: float) -> str:
    """一致度区分(05 §2)。high/medium/low — 下端は運用閾値に連動し未定義区間なし。"""
    if score >= MATCH_LEVEL_HIGH_MIN:
        return "high"
    if score >= MATCH_LEVEL_MEDIUM_MIN:
        return "medium"
    return "low"


def pair_target_time(time_start_a: datetime, time_start_b: datetime) -> datetime:
    """対象開始時刻 = max(参加Intentのtime_start)(承認済み解釈・design §2.4)。

    75分ルール・D-05式・defer抑制の残時間・提示順ソートがすべてこの値を参照。
    """
    return max(time_start_a, time_start_b)


def d07_history_inputs(responses: list[dict]) -> tuple[bool, datetime | None]:
    """latches.responses(JSONB復元済みlist)からD-07判定入力を抽出(純関数)。

    has_no: response='no' が1つでも存在。latest_defer_at: response='defer' の
    answered_at(ISO文字列)の最大(None=defer履歴なし)。answered_at欠損の
    defer要素は無視する(防御・M3-1が必ず書く)。
    """
    has_no = any(r.get("response") == RESPONSE_NO for r in responses)
    defers = [
        datetime.fromisoformat(r["answered_at"])
        for r in responses
        if r.get("response") == RESPONSE_DEFER and r.get("answered_at")
    ]
    return has_no, (max(defers) if defers else None)


def d07_allows(
    *,
    has_no_response: bool,
    latest_defer_at: datetime | None,
    now: datetime,
    target_time: datetime,
    new_score: float,
    prev_latch_score: float | None,
) -> bool:
    """D-07再提案判定(03 D-07・06 §10・design §2.4)。True=提案してよい。

    判定順: (1)no履歴→False(2)defer履歴なし→True(3)抑制期間
    min(24時間,(対象開始時刻−now)/2)経過→True(4)抑制期間内はスコア変化判定
    (prevがNone=新評価世代なら無条件変化あり・非Noneなら|新−prev|≧0.05)。
    """
    if has_no_response:
        return False
    if latest_defer_at is None:
        return True
    suppress = min(DEFER_SUPPRESS_MAX, (target_time - now) / 2)
    if now - latest_defer_at >= suppress:
        return True
    if prev_latch_score is None:
        return True
    return abs(new_score - prev_latch_score) >= D07_DELTA


def bucket_start(now: datetime) -> datetime:
    """現在時刻の属する30分Bucketの開始時刻(06 §9・design §2.8-2)。

    JSTへ変換し分を0/30へ切り下げ。戻り値はJST tz-aware(Bucket境界はClock由来)。
    """
    jst_now = now.astimezone(JST)
    minute = 0 if jst_now.minute < BUCKET_MINUTES else BUCKET_MINUTES
    return jst_now.replace(minute=minute, second=0, microsecond=0)
