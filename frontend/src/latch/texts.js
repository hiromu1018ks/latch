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
export const ATTENDANCE_QUESTION = "実際に会いましたか?"; // D-09
export const ATTENDANCE_YES_LABEL = "会いました";
export const ATTENDANCE_NO_LABEL = "会えていません";
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

// 回答操作2択(03 §5のUI文言→API値・D-05で「辞退する」(no)のUI導線は廃止。
// APIの3値は温存・noはAPI直叩きでのみ到達)
export const RESPONSE_BUTTONS = [
  ["yes", "参加する"],
  ["defer", "今回は見送る"],
];

// 回答済み表示(design §2.4)
export const ANSWERED_TEXT = {
  yes: "参加します",
  defer: "今回は見送りました",
  no: "辞退しました",
};

// --- UI v2 Phase 3追記: 04候補が届く(D-05〜D-07・プロトタイプ04が正源) ------

// 4状態の見出し(プロトタイプ04のdecideカード)
export const PROPOSAL_HERO_TEXT = "条件が、揃いそうです。";
export const PROPOSAL_LEAD_TEXT =
  "相手の姿は、全員が「参加する」と答えたときにはじめて見えます。あなたの条件も、まだ誰にも渡っていません。";
export const JOINED_TITLE = "参加と回答しました。";
export const ALL_IN_TITLE = "全員が参加しました。";
export const DECLINED_TITLE = "見送りました。";
export const FIT_TITLE = "あなたの条件との照合";
export const FIT_ALL_TEXT = "5項目すべて条件内";
export const FIT_NOTE_TEXT =
  "この案は、ほかの参加候補それぞれの条件にも収まっています。片方だけが大満足する提案は出しません。";
export const FIT_UNAVAILABLE_TEXT = "候補は、あなたの条件の範囲で選ばれています";
export const MINIMAL_PROPOSAL_NOTE =
  "くわしい内容は、全員が「参加する」と答えたときにわかります";
export const RESPONSE_NOTE_TEXT =
  "全員が「参加する」と答えたときだけ、相手が見えてチャットが開きます。あなたの回答は、次の提案の精度に活かされます。";
export const JOINED_WAIT_NOTE =
  "誰かが見送った場合は、何も起きずに待機に戻ります。誰が見送ったかは、お互いに分かりません。";
export const JOINED_DONE_NOTE = "全員がそろったら、お知らせします。";
export const DECLINED_NOTE_TEXT =
  "条件はそのまま、待機を続けます。見送ったことは、ほかの参加候補には伝わりません。";
export const ALL_IN_LEAD_TEXT = "LATCHが成立しました。ここから、お互いの姿が見えます。";
export const OPEN_CHAT_LABEL = "チャットを開く";
export const BACK_TO_STANDBY_LABEL = "待機中に戻る";
export const UNDO_JOINED_LABEL = "回答を取り消す";
export const UNDO_DECLINED_LABEL = "取り消す";
export const UNDO_BLOCKED_TOAST_TEXT = "この回答は取り消せませんでした";
export const DEADLINE_LINE_SUFFIX = " を過ぎると、この候補は静かに消えます";

// 見送り理由chips(D-07・値はAPIのtime/place/size/mood・任意複数)
export const REASON_CHIPS = [
  ["time", "時間が合わない"],
  ["place", "場所が合わない"],
  ["size", "人数が合わない"],
  ["mood", "今日は気分じゃない"],
];
export const REASON_LABEL_TEXT = "よければ理由を教えてください（任意・複数可）";
export const REASON_NOTE_TEXT = "次の提案をより合うものにするために使います";

// --- UI v2 Phase 3追記: 03待機中(プロトタイプ03が正源) ----------------------

export const STANDBY_TITLE_A = "条件が揃うのを、";
export const STANDBY_TITLE_B = "待っています。";
export const STANDBY_DEPOSITED_A = "預けました。";
export const STANDBY_DEPOSITED_B = "あとは、待つだけ。";
export const STANDBY_LEAD =
  "あなたが預けた条件です。別の誰かの条件と成立し得ると判断された瞬間にだけ、お知らせします。";
export const STANDBY_DEPOSITED_LEAD =
  "LATCHがこの条件を預かっています。別の誰かの条件と成立し得ると判断された瞬間にだけ、お知らせします。";
export const STANDBY_PILL_TEXT = "待機中";
export const WITHDRAWN_PILL_TEXT = "取り下げ済み";
export const WITHDRAWN_NOTE_TEXT =
  "この条件は取り下げました。もう誰とも照らし合わされません。";
export const DEPOSIT_AGAIN_LABEL = "もう一度預ける";
export const EDIT_CONDITION_LABEL = "条件を直す";
export const WITHDRAW_BUTTON_LABEL = "取り下げる";
export const NEW_DEPOSIT_LABEL = "新しく預ける";
export const NO_OTHERS_TEXT = "ほかに待機中の意思はありません。";
export const WITHDRAW_DIALOG_TITLE = "この条件を取り下げますか？";
export const WITHDRAW_DIALOG_NOTE =
  "取り下げると、照らし合わせを止めます。届いている候補も消えます。相手に通知されることはありません。";
export const WITHDRAW_CANCEL_LABEL = "やめておく";
export const WITHDRAWN_TOAST_TEXT = "取り下げました";
export const CANDIDATE_TOAST_TEXT = "LATCH候補があります"; // 03の右上トースト
export const CANDIDATE_TOAST_SEE_LABEL = "見る";

// 見送り理由の選択式4値(08 §5.2・API値はws-5実装の英語コード)
export const REPORT_REASON_OPTIONS = [
  ["inappropriate_content", "不適切な内容"],
  ["unpleasant_behavior", "不快な対応"],
  ["suspected_impersonation", "なりすまし疑い"],
  ["other", "その他"],
];

// --- UI v2 Phase 4追記: 05成立・チャット(D-08〜D-10・プロトタイプ05が正源) ---

// 成立ヒーロー(kicker=「成立」・取消後は題とリードを差し替える)
export const LATCHED_TITLE = "LATCHしました。";
export const LEFT_TITLE = "参加を取り消しました。";
export const LEFT_LEAD_TEXT =
  "このチャットには書き込めません。条件は待機に戻していません。必要なら、もう一度預けてください。";

// 成立した内容(details+dl)
export const LATCHED_DETAILS_TITLE = "成立した内容";
export const LATCHED_PLACE_SUB_TEXT = "集合場所はチャットで";
export const CALENDAR_BUTTON_TEXT = "カレンダーに追加"; // D-10(クライアント完結)

// メンバー(色付きイニシャルアバター)
export const MEMBER_NOTE_TEXT =
  "成立したので、お互いの表示名が見えるようになりました。";
export const MEMBER_JOINED_TEXT = "参加";
export const MEMBER_LEFT_TEXT = "取り消し済み";

// 安全のために+通報・ブロック(D-09: 理由4択はdialog内ステップ)
export const SAFETY_HEADING_TEXT = "安全のために";
export const SAFETY_GUIDE_TITLE = "会う前の安全ガイド";
export const SAFETY_GUIDE_ITEMS = [
  "はじめは人通りの多いお店で会いましょう",
  "連絡先の交換は、会ってからでも遅くありません",
  "違和感があれば、無理せず帰って大丈夫です",
];
export const SAFETY_ENTRY_TEXT = "通報・ブロック"; // aria-haspopup=dialog
export const SAFETY_DIALOG_TITLE = "通報・ブロック";
export const SAFETY_TARGET_LABEL = "対象のメンバー";
export const SAFETY_NOTE_TEXT =
  "ブロックすると、この相手とは今後LATCHが成立しなくなります。相手には通知されません。通報は運営が確認し、必要に応じて対応します。";
export const SAFETY_REASON_LEGEND = "理由を選んでください";
export const SAFETY_REASON_NOTE = "通報の理由は運営の確認に使います(相手には伝わりません)";
export const SAFETY_BACK_LABEL = "もどる";
export const SAFETY_BLOCK_LABEL = "ブロックする";
export const SAFETY_REPORT_LABEL = "通報する";
export const SAFETY_CANCEL_LABEL = "キャンセル";
export const SAFETY_CLOSE_LABEL = "閉じる";
export const SAFETY_REASON_ERROR_TEXT = "理由を選んでください。";
export const SAFETY_TARGET_ERROR_TEXT = "相手を選んでください。";
export const SAFETY_ERROR_TEXT = "送信できませんでした。";

// この成立を取り消す(D-08: POST /v1/latches/{id}/leave・自分の参加のみ)
export const CANCEL_ENTRY_TEXT = "この成立を取り消す";
export const CANCEL_DIALOG_TITLE = "この成立を取り消しますか？";
export const CANCEL_DIALOG_INTRO = "取り消すと、次のようになります。";
export const CANCEL_DIALOG_NOTE = "この操作は元に戻せません。";
export const CANCEL_DIALOG_DISMISS = "やめておく";
export const CANCEL_DIALOG_CONFIRM = "取り消す";

// チャット(プロトタイプ05の構成)
export const CHAT_OPENING_TEXT = "LATCHが成立し、チャットが開きました";
export const CHAT_PLACEHOLDER_TEXT = "メッセージを送る";
export const CHAT_LEFT_PLACEHOLDER_TEXT = "取り消したため、書き込めません";
export const CHAT_SEND_ERROR_TEXT = "送信できませんでした。";
export const CHAT_EMPTY_ERROR_TEXT = "メッセージを入力してください。";

// --- UI v2 Phase 5追記: 横断機能(D-13・D-14・D-11・v2部品の独自構成分) -------

// 下書き(03の「ほかに待機中の意思」の下の行・D-13の最小導線)。
// 取り下げる=WITHDRAW_BUTTON_LABEL・直す系の文言は下書きはactive化を伴うため別語
export const DRAFT_SECTION_TITLE = "下書き";
export const DRAFT_PILL_TEXT = "下書き";
export const DRAFT_EDIT_LABEL = "直して預ける";

// D-11簡易一覧(候補・成立タブ・phase0-decisions D-11)。見出しは通知文言の
// 承認済み語彙(PROPOSAL_NOTICE_TITLE・MATCHED_NOTICE_TITLE)を流用し、
// バッジと空状態は既存定数(NO_LATCH_*・NO_MATCHED_*)を使う
export const LIST_PROPOSALS_BADGE = "LATCH候補";
export const LIST_MATCHED_BADGE = "成立";

// --- M3 ws-8追記: お知らせ・設定(design §2.8) -----------------------------

// お知らせpopover(03 §2・§4の様式・design §2.3)
export const NOTICE_EMPTY_TITLE = "まだ新しい候補はありません";
export const NOTICE_EMPTY_NOTE = "条件が合うと、ここに静かに届きます。";
export const PROPOSAL_NOTICE_TITLE = "LATCH候補があります。"; // 03 §4の様式
export const MATCH_NOTICE_TEXT = "一致度が高い候補です。"; // 03 §4の様式
export const NEARBY_NOTICE_TEXT = "近い条件の候補があるようです。"; // §5-2①(設計判断)
export const NOTICE_FALLBACK_TEXT = "LATCHからのお知らせがあります。"; // latch=null防御
export const MATCHED_NOTICE_TITLE = "LATCHが成立しました。"; // 成立通知行(m3-g3fix裁定2)

// 通知許可(design §2.5・D-18のM3での範囲=ブラウザ許可状態のみ)
export const PERMISSION_GRANTED_TEXT = "通知は許可されています";
export const PERMISSION_DEFAULT_TEXT = "通知は許可されていません";
export const PERMISSION_DENIED_TEXT =
  "通知はブラウザの設定でブロックされています。ブラウザの設定から変更できます。";
export const PERMISSION_UNSUPPORTED_TEXT = "この環境では通知を利用できません。";
export const PERMISSION_NOTE_TEXT = "許可しなくても、アプリ内のお知らせで確認できます。";
export const PERMISSION_REQUEST_LABEL = "通知の許可を求める";

// プッシュ登録(05 v0.9・V-01)。{last4}はpushRegistrationのtokenLast4。
export const PUSH_REGISTERED_TEXT = "この端末で通知を受け取ります";
export const PUSH_NO_PERMISSION_TEXT =
  "通知の許可がないため、プッシュ通知の登録はありません。お知らせはアプリ内で届きます。";
export const PUSH_UNAVAILABLE_TEXT = "この環境ではプッシュ通知を利用できません。";
export const PUSH_ERROR_TEXT = "プッシュ通知の登録に失敗しました。";

// nearby設定(03 v0.6・設定画面のトグル)
export const NEARBY_SETTING_LABEL = "近い条件の候補があれば知らせる";
export const NEARBY_SETTING_NOTE =
  "閾値に達しなかった近い条件の候補がいるとき、その存在だけをお知らせに追加します。内容や相手は表示されません。";

// ブロック管理・登録(design §2.6〜§2.7)
export const BLOCKS_EMPTY_TEXT = "ブロックしているユーザーはいません";
export const BLOCK_UNBLOCK_LABEL = "解除する";
export const BLOCK_CANCEL_LABEL = "やめる";
export const BLOCK_CONFIRM_SUFFIX = "さんのブロックを解除しますか?"; // 前に表示名を結合
export const BLOCK_CONFIRM_TEXT = "ブロックすると、このやりとりは利用できなくなります。";
export const BLOCK_SELECT_ERROR_TEXT = "相手を選んでください。";
export const BLOCK_ERROR_TEXT = "送信できませんでした。";

// --- M4 ws-5追記: 告知・同意(08 D-14・2026-10-03ユーザー承認済み文言) -----

export const CONSENT_TITLE = "LLM送信の同意";
export const CONSENT_LEAD_TEXT =
  "LATCHでは、あなたが入力したIntentの内容を、マッチング判定のために外部のAIサービスへ送信します。";
export const CONSENT_DESTINATIONS_TEXT =
  "送信先: TypeSafe AI(マッチングの判定)/ Anthropic(条件の読み取り・判定の予備)/ Google Gemini(条件の数値化)";
export const CONSENT_SCOPE_TEXT =
  "送信するのは判定に必要な最小限の情報で、送信記録を保持します(お問い合わせいただければ開示します)";
export const CONSENT_NO_TRAINING_TEXT = "送信データは、AIサービスの学習には利用されません";
export const CONSENT_CONDITION_TEXT =
  "この同意は、マッチング機能を利用するための条件です。同意がない場合、Intentを預ける(マッチングに供給する)ことはできません。";
export const CONSENT_CHECK_LABEL = "上記に同意します";
export const CONSENT_AGREE_LABEL = "同意する";
export const CONSENT_STATE_AGREED_TEXT = "同意済み: ";
export const CONSENT_STATE_NOT_AGREED_TEXT = "同意していません";
export const CONSENT_TOAST_TEXT = "同意を記録しました";
export const CONSENT_REQUIRED_TOAST_TEXT =
  "マッチングの利用には同意が必要です。同意画面へ移動します。";

// --- V1-P3追記: 体感アンケート(05 v0.7・09 §2.3・仮説1/5) --------------------
// 配信は03待機中への受動カード(モーダル・通知・バッジなし)。設問は1回3問以内:
// onboarding=便利さ1問・week4=便利さ+継続意向+任意の自由記述。

export const SURVEY_ONBOARDING_TITLE = "はじめての待ちかた、いかがでしたか?";
export const SURVEY_WEEK4_TITLE = "4週間、待ってみていかがでしたか?";
export const SURVEY_CONVENIENCE_QUESTION =
  "条件を預けて待つだけでLATCH候補が届く仕組みを、便利だと思いますか?";
export const SURVEY_CONTINUATION_QUESTION =
  "今後もLATCHを使い続けたいと思いますか?";
export const SURVEY_SCALE_OPTIONS = [
  ["1", "まったく思わない"],
  ["2", "あまり思わない"],
  ["3", "どちらとも言えない"],
  ["4", "思う"],
  ["5", "とても思う"],
];
export const SURVEY_FREE_COMMENT_LABEL = "ひとこと、お聞かせください（任意）";
export const SURVEY_FREE_COMMENT_PLACEHOLDER = "待つ体験について感じたことなど";
export const SURVEY_NOTE_TEXT =
  "回答はLATCHの体験を良くするために使います。回答と、あなたが預けた条件が結びつけられることはありません。";
export const SURVEY_SUBMIT_LABEL = "送信する";
export const SURVEY_DONE_TEXT = "回答を送りました";
export const SURVEY_ERROR_TEXT = "送信できませんでした。";
