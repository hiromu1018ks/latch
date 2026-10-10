// API応答→表示への純関数群(M3 ws-7 design §2.3〜§2.4)。DOM非依存。
// フロントで新たな計算をしない — 一致度はmatch_level・残時間は
// response_deadlineの書式変換のみ・visibility分岐はproposalの
// time_summaryキー有無(生成側の2形と1:1)。
import { formatCategory, formatParticipants, jstParts } from "../intent/format.js";
import {
  ANSWERED_TEXT,
  ATTENDANCE_QUESTION,
  CLOSED_TEXT,
  DEADLINE_CLOSED_TEXT,
  DEADLINE_SOON_TEXT,
  HIDDEN_PROPOSAL_TEXT,
  MATCH_NOTICE_TEXT,
  MATCHED_NOTICE_TITLE,
  MATCH_LEVEL_TEXT,
  NEARBY_NOTICE_TEXT,
  NEXT_ACTION_TEXT,
  NOTICE_FALLBACK_TEXT,
  PROPOSAL_NOTICE_TITLE,
  STATUS_TEXT,
} from "./texts.js";

const PROPOSAL_STATUSES = new Set(["proposed", "partial_accept"]);
const MATCHED_STATUSES = new Set(["matched", "completed"]);

export const latchMode = (latch) =>
  PROPOSAL_STATUSES.has(latch.status)
    ? "proposal"
    : MATCHED_STATUSES.has(latch.status)
      ? "matched"
      : "closed";

export const isMinimalProposal = (latch) =>
  !("time_summary" in latch.proposal); // hidden_until_matchを含む形

export const matchLevelText = (latch) =>
  MATCH_LEVEL_TEXT[latch.proposal.match_level] ?? "";

export const remainingMs = (deadlineIso, nowIso) =>
  new Date(deadlineIso).getTime() - new Date(nowIso).getTime();

export const remainingTimeText = (deadlineIso, nowIso) => {
  const ms = remainingMs(deadlineIso, nowIso);
  if (ms <= 0) return DEADLINE_CLOSED_TEXT;
  const totalMin = Math.floor(ms / 60000); // 秒は表示しない
  if (totalMin < 1) return DEADLINE_SOON_TEXT;
  if (totalMin < 60) return `あと${totalMin}分で締切`;
  if (totalMin < 60 * 24) {
    const hours = Math.floor(totalMin / 60);
    const minutes = totalMin % 60;
    return minutes === 0
      ? `あと${hours}時間で締切`
      : `あと${hours}時間${minutes}分で締切`;
  }
  const days = Math.floor(totalMin / (60 * 24));
  const hours = Math.floor((totalMin % (60 * 24)) / 60);
  return hours === 0
    ? `あと${days}日で締切`
    : `あと${days}日と${hours}時間で締切`;
};

export const groupNeedText = (latch) =>
  latch.is_group && latch.remaining_responses > 0
    ? `あと${latch.remaining_responses}人の回答が必要`
    : null;

// 条件サマリ(全フィールド版のみ・design §2.4末尾の書式)。
// raw_text・NG条件・座標・距離は組まない(引用#20の#22・#23)。
// area_name等のnullは行ごと組まない(逆ジオコーディング不成立の状態)。
export const conditionSummaryLines = (latch) => {
  if (isMinimalProposal(latch)) return null;
  const p = latch.proposal;
  const lines = [
    p.time_summary,
    p.area_name,
    `${p.headcount}人`,
    formatCategory({
      primary: p.category_primary,
      secondary: p.category_secondary,
    }),
  ].filter((line) => line != null && line !== "");
  if (p.budget && p.budget.max != null) {
    lines.push(`ひとり${Number(p.budget.max).toLocaleString("ja-JP")}円まで`);
  }
  return lines;
};

// 不成立の二値化(03 §7): 自分の操作履歴か統一文言か
export const closedText = (latch) =>
  latch.my_response === "no"
    ? ANSWERED_TEXT.no
    : latch.my_response === "defer"
      ? ANSWERED_TEXT.defer
      : CLOSED_TEXT;

export const messageSide = (message, meId) =>
  message.sender_id === meId ? "mine" : "theirs";

export const senderName = (senderId, participants) =>
  participants.find((p) => p.user_id === senderId)?.display_name ?? "";

// API応答由来の文字列をinnerHTMLへ入れる前のエスケープ(XSS対策)
export const escapeHtml = (value) =>
  String(value).replace(/[&<>"']/g, (c) => (
    { "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]
  ));

// --- M3 ws-8追記: お知らせ行の純関数(design §2.3) ---------------------------

// created_atのJST書式(お知らせ行・ブロック日の日付表示。年は出さない)
export const noticeTimeText = (iso) => {
  const p = jstParts(iso);
  return `${p.month}月${p.day}日 ${p.hour}:${p.minute}`;
};

const CLOSED_STATUSES = new Set(["expired", "rejected", "cancelled"]);
const MATCHED_NOTICE_STATUSES = new Set(["matched", "completed"]);

// お知らせ行の文言(type×latch.statusのマトリクス・design §2.3・m3-g3fix §2.5)。
// 終了行はclosedText(latch)で自分の操作履歴(no/defer)へ分岐(03 §7・⑫)。
// nearbyは存在通知のみ。latch_matchedは見出し+導線+バッジ(条件サマリ不含)。
export const notificationLines = (item, nowIso) => {
  if (item.type === "nearby_candidate") return [NEARBY_NOTICE_TEXT];
  if (item.type === "attendance_request") return [ATTENDANCE_QUESTION];
  const latch = item.latch;
  if (!latch) return [NOTICE_FALLBACK_TEXT]; // 防御(全type)
  if (item.type === "latch_matched") {
    // 解散後(statusがexpired等)は一文統一(03 §7)。条件サマリは成立通知行に
    // 含めない(hidden_until_match提案は最小構造のままで表示可否が分かれる)
    if (CLOSED_STATUSES.has(latch.status)) return [CLOSED_TEXT];
    return [
      MATCHED_NOTICE_TITLE,
      NEXT_ACTION_TEXT, // 成立後詳細と同一の導線文言(文言単一ソース)
      STATUS_TEXT[latch.status] ?? latch.status, // バッジ(成立済み/完了)
    ];
  }
  if (CLOSED_STATUSES.has(latch.status)) return [closedText(latch)]; // 操作履歴へ分岐
  if (MATCHED_NOTICE_STATUSES.has(latch.status)) {
    const title = isMinimalProposal(latch)
      ? HIDDEN_PROPOSAL_TEXT
      : [latch.proposal.time_summary, latch.proposal.area_name]
          .filter(Boolean)
          .join(" ");
    return [
      title,
      ...(conditionSummaryLines(latch) ?? []),
      STATUS_TEXT[latch.status] ?? latch.status, // バッジ(回答促しは出さない)
    ];
  }
  // proposed / partial_accept
  if (isMinimalProposal(latch)) {
    return [
      HIDDEN_PROPOSAL_TEXT,
      MATCH_NOTICE_TEXT,
      remainingTimeText(latch.response_deadline, nowIso),
    ];
  }
  return [
    PROPOSAL_NOTICE_TITLE,
    ...(conditionSummaryLines(latch) ?? []),
    MATCH_NOTICE_TEXT,
  ];
};

// タップ先(nearbyとlatch=nullはリンクなし・引用#20)
export const notificationHref = (item) => {
  if (item.type === "nearby_candidate") return null;
  if (!item.latch) return null;
  return `#/latches/${item.latch.id}`;
};

// --- UI v2 Phase 3追記: 04候補が届く(プロトタイプ04が正源の純関数群) --------

// 4状態(open/joined/allIn/declined)。allIn=自分のyesで全員がそろった状態
// (status=matched・詳細画面のチャットは「チャットを開く」で開く)。
// declinedはmy_response=defer(statusは即時rejectedになるためclosed姿からも
// 同関数で状態名を取り出す)
export const proposalStateOf = (latch) => {
  if (latch.my_response === "yes") {
    return latch.remaining_responses === 0 ? "allIn" : "joined";
  }
  if (latch.my_response === "defer") return "declined";
  return "open";
};

// JSTの時刻部分(HH:MM)。作成時刻バッジ・締切注記に使う
export const clockText = (iso) => {
  const p = jstParts(iso);
  return `${p.hour}:${p.minute}`;
};

// 「今夜 · 10/10（土）」「明日 · 10/11（日）」「10/12（月）」(4情報カードのsub)
export const proposalDateText = (
  timeSummary,
  nowIso = new Date().toISOString(),
) => {
  // time_summaryは「YYYY-MM-DD HH:MM」(JST・proposal.pyの格納形式)
  const match = /^(\d{4})-(\d{2})-(\d{2})/.exec(String(timeSummary ?? ""));
  if (!match) return null;
  const [, year, month, day] = match;
  const iso = `${year}-${month}-${day}T12:00:00+09:00`; // 昼固定で日付差を安定化
  const p = jstParts(iso);
  const now = jstParts(nowIso);
  const diff =
    Math.round(
      (Date.UTC(p.year, p.month - 1, p.day) -
        Date.UTC(now.year, now.month - 1, now.day)) /
        86_400_000,
    );
  const date = `${p.month}/${p.day}（${{ Sun: "日", Mon: "月", Tue: "火", Wed: "水", Thu: "木", Fri: "金", Sat: "土" }[p.weekday] ?? ""}）`;
  return diff === 0 ? `今夜 · ${date}` : diff === 1 ? `明日 · ${date}` : date;
};

// proposal.time_summary → 4情報カードの「開始」(clock=20:30・date=今夜 · …)
export const proposalTimeParts = (
  timeSummary,
  nowIso = new Date().toISOString(),
) => {
  const match = /^.* (\d{2}:\d{2})$/.exec(String(timeSummary ?? ""));
  if (!match) return null;
  return { clock: match[1], date: proposalDateText(timeSummary, nowIso) };
};

// 「¥4,000」(予算カード・オーブのチップ)
export const formatYen = (max) => `¥${Number(max).toLocaleString("ja-JP")}`;

// 残時間の桁あふれなし書式(03=HH:MM:SS・04=MM:SS)。負は0固定
export const hhmmssText = (ms) => {
  const total = Math.max(0, Math.floor(ms / 1000));
  const pad = (n) => String(n).padStart(2, "0");
  return `${pad(Math.floor(total / 3600))}:${pad(Math.floor((total % 3600) / 60))}:${pad(total % 60)}`;
};
export const countdownText = (ms) => {
  const total = Math.max(0, Math.floor(ms / 1000));
  const mm = Math.floor(total / 60);
  const ss = String(total % 60).padStart(2, "0");
  return `${mm}:${ss}`;
};

// 参加者行(目隠しアバタースタックの下の一文・プロトタイプ04のwhoLine)
export const whoLineText = (state, latch) => {
  const total = latch.proposal?.headcount ?? 2;
  if (state === "joined") return `あなた（参加）＋ 回答待ちの${total - 1}人`;
  if (state === "allIn") return `${total}人全員が参加`;
  return `あなた ＋ まだ見えない${total - 1}人`;
};

// joined状態の現況文(「ほかの2人の回答を待っています（1人が回答済み）。」+
// 完了時の一文。1対1や回答済み0は括弧を組まない)
export const joinedWaitText = (latch) => {
  const total = latch.proposal?.headcount ?? 2;
  const answered = Math.max(0, total - latch.remaining_responses - 1);
  const head = `ほかの${total - 1}人の回答を待っています`;
  return `${head}${answered > 0 ? `（${answered}人が回答済み）` : ""}。全員がそろったら、お知らせします。`;
};

// 自分のIntentの候補あて(gap分析§6: 詳細応答にintent_idsがないため、
// active Intentを値マッチングで特定する — D-03の値マッチングと同じ発想)。
// カテゴリ一致+場所の包含+予算の収まり+人数の収まりで採点し、正点のみ。
// 該当なしがnull(照合表は「あなたの条件」列を「—」で出す)
export const pickMyIntent = (intents, latch) => {
  const p = latch?.proposal ?? {};
  let best = null;
  let bestScore = 0;
  for (const intent of intents ?? []) {
    const s = intent.structured_intent ?? {};
    let score = 0;
    if (p.category_primary && s.category?.primary === p.category_primary) score += 2;
    const myPlace = s.location?.name;
    if (myPlace && p.area_name && (p.area_name.includes(myPlace) || myPlace.includes(p.area_name))) {
      score += 2;
    }
    if (s.budget?.max != null && p.budget?.max != null && p.budget.max <= s.budget.max) {
      score += 2;
    }
    const people = formatParticipants(s.participants);
    if (
      people !== "指定なし" &&
      p.headcount != null &&
      (s.participants?.min ?? 0) <= p.headcount &&
      p.headcount <= (s.participants?.max ?? 99)
    ) {
      score += 1;
    }
    if (score > bestScore) {
      best = intent;
      bestScore = score;
    }
  }
  // 人数だけの一致(headcount=2は既定値)では特定したことにならないため
  // 弱いシグナル(カテゴリ・場所・予算のいずれか+α)を要求する
  return bestScore >= 2 ? best : null; // 同点は先勝り(created_at降順=新しい方)
};

// 照合表の1行組立(自分の条件→矢印→候補)。ok=双方の値が揃った行のみ✓。
// 自分のIntentが特定できない行はmine=「—」・ok=false(見出しの文言を替える)
export const fitRows = (myIntent, latch, nowIso) => {
  const p = latch?.proposal ?? {};
  const s = myIntent?.structured_intent ?? {};
  const mine = {
    時間: s.time?.start
      ? `${shortDayWord(s.time.start, nowIso)}${clockText(s.time.start)}${s.time.end ? `〜${clockText(s.time.end)}` : " 以降"}`
      : "—",
    場所: s.location?.name ?? "—",
    人数: s.participants
      ? `${formatParticipants(s.participants)}${s.participants_includes_owner === false ? "" : "（あなたを含む）"}`
      : "—",
    予算: s.budget?.max != null ? `${Number(s.budget.max).toLocaleString("ja-JP")}円まで` : "—",
    目的: p.category_primary != null || s.category
      ? (s.category?.secondary ?? (s.category?.primary ? formatCategory(s.category) : "—"))
      : "—",
  };
  const theirs = {
    時間: proposalTimeParts(p.time_summary)?.clock
      ? `${proposalTimeParts(p.time_summary).clock} 開始`
      : (p.time_summary ?? "—"),
    場所: p.area_name ?? "—",
    人数: p.headcount != null ? `${p.headcount}人` : "—",
    予算: p.budget?.max != null ? `${Number(p.budget.max).toLocaleString("ja-JP")}円前後` : "—",
    目的: p.category_secondary ?? (p.category_primary ? formatCategory({ primary: p.category_primary }) : "—"),
  };
  const labels = ["時間", "場所", "人数", "予算", "目的"];
  return labels.map((label) => ({
    label,
    mine: mine[label],
    theirs: theirs[label],
    ok: mine[label] !== "—" && theirs[label] !== "—",
  }));
};

const shortDayWord = (startIso, nowIso) => {
  if (!nowIso) return "";
  const p = jstParts(startIso);
  const now = jstParts(nowIso);
  const diff = Math.round(
    (Date.UTC(p.year, p.month - 1, p.day) - Date.UTC(now.year, now.month - 1, now.day)) /
      86_400_000,
  );
  return diff === 0 ? "今日" : diff === 1 ? "明日" : "";
};
