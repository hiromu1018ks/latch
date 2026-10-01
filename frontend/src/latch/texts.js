// 画面文言の定数集約(M3 ws-7 design §2.8)。
// 「この提案は成立しませんでした」等の一文統一はws-8がこの定数を
// 参照して実現する(単一ソース)。文言変更はこの1ファイルに集約される。

export const CLOSED_TEXT = "この提案は成立しませんでした";
export const CHAT_UNAVAILABLE_TEXT = "このチャットは利用できません"; // D-23
export const CHAT_COMPLETED_TEXT =
  "対象時刻を過ぎたため、このチャットは閲覧のみできます";
export const HIDDEN_PROPOSAL_TEXT = "条件が合う候補があります"; // 03 §4の様式
export const NO_LATCH_TITLE = "まだ提案はありません";
export const NO_LATCH_NOTE = "条件が重なると、ここに届きます。";
export const NO_MATCHED_TITLE = "成立したLATCHはまだありません";
export const NO_MATCHED_NOTE = "成立すると、ここに並びます。";
export const LATCH_NOT_FOUND_TEXT = "提案が見つかりません"; // 404/403同一扱い
export const HOME_LINK_TEXT = "ホームへ戻る";
export const NEXT_ACTION_TEXT = "チャットで挨拶を交わしましょう";
export const DEADLINE_CLOSED_TEXT = "締切";
export const DEADLINE_SOON_TEXT = "まもなく締切";
export const RATE_LIMIT_TEXT =
  "操作が集中しています。少し時間をおいてもう一度お試しください。";
export const REPORT_TOAST_TEXT = "通報を受け付けました";
export const ATTENDANCE_QUESTION = "実際に会いましたか?"; // D-09
export const ATTENDANCE_DONE_TEXT = "回答を送りました";
export const ATTENDANCE_ALREADY_TEXT = "回答済みです";
export const ATTENDANCE_ERROR_TEXT = "送信できませんでした。";

// 一致度区分(03 §5「高」等の表示・生スコアは出さない)
export const MATCH_LEVEL_TEXT = { high: "高", medium: "中", low: "低" };

// status→バッジ文言(candidateは一覧に出ないため含めない)
export const STATUS_TEXT = {
  proposed: "回答まち",
  partial_accept: "回答まち",
  matched: "成立済み",
  completed: "完了",
};

// 回答操作3択(03 §5のUI文言→API値・design §2.5)
export const RESPONSE_BUTTONS = [
  ["yes", "参加する"],
  ["defer", "今回は見送る"],
  ["no", "辞退する"],
];

// 回答済み表示(design §2.4)
export const ANSWERED_TEXT = {
  yes: "参加します",
  defer: "今回は見送りました",
  no: "辞退しました",
};

// 通報理由の選択式4値(08 §5.2・API値はws-5実装の英語コード)
export const REPORT_REASON_OPTIONS = [
  ["inappropriate_content", "不適切な内容"],
  ["unpleasant_behavior", "不快な対応"],
  ["suspected_impersonation", "なりすまし疑い"],
  ["other", "その他"],
];
