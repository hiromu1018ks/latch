"""プッシュ文面テンプレート(M3 ws-3 design §2.4案A)。

build_pushの引数はnotification_typeのみ(03 §4・08 §2.6 FR-21の構造担保)。
proposal/latchのデータ(時間・地域・人数・カテゴリ・予算・スコア)を
受け取る引数は存在しない — 条件サマリを本文へ混ぜる経路が型の上で
存在しない。文言の変更はこの定数のみ(A/B対象「通知文」の変更に構造が
引かれない・05 §2)。
"""

from latch.notifications.types import (
    NOTIFICATION_ATTENDANCE_REQUEST,
    NOTIFICATION_NEARBY,
    NOTIFICATION_PROPOSAL,
)

PUSH_TITLE = "LATCH"
PUSH_BODY_LATCH = "LATCH候補があります。\n詳細はアプリでご確認ください。"
PUSH_BODY_NOTICE = "LATCHからのお知らせがあります。\n詳細はアプリでご確認ください。"


def build_push(notification_type: str) -> tuple[str, str]:
    """type→(title, body)の写像(design §2.4)。

    proposal/nearby_candidateは同一文言(文面を分けると「閾値未満の候補
    である」ことがOS経路〔ロック画面等〕に漏れるため・承認②)。
    attendance_requestは第二汎用文(08 §2.6趣旨の準用・承認①)。
    未知typeはValueError(実装バグの即時顕在化・design §2.7)。
    """
    if notification_type in (NOTIFICATION_PROPOSAL, NOTIFICATION_NEARBY):
        return PUSH_TITLE, PUSH_BODY_LATCH
    if notification_type == NOTIFICATION_ATTENDANCE_REQUEST:
        return PUSH_TITLE, PUSH_BODY_NOTICE
    raise ValueError(f"unknown notification_type: {notification_type!r}")
