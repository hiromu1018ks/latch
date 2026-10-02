"""通知type定数の正本(M3 ws-3 design §3.1)。

latch_engine(NOTIFICATION_PROPOSAL/NEARBY)・sweeper
(NOTIFICATION_ATTENDANCE_REQUEST)・latches serviceの成立通知
(NOTIFICATION_MATCHED・m3-g3fix)がimport切替で参照する単一ソース。
値はws-6/ws-2実装時の文字列と同一(既存試験の文字列リテラルと無干渉)。
"""

NOTIFICATION_PROPOSAL = "proposal"
NOTIFICATION_NEARBY = "nearby_candidate"
NOTIFICATION_ATTENDANCE_REQUEST = "attendance_request"
NOTIFICATION_MATCHED = "latch_matched"  # m3-g3fix裁定4(既存3値の命名規律に同じ)
