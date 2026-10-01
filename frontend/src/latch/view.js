// API応答→表示への純関数群(M3 ws-7 design §2.3〜§2.4)。DOM非依存。
// フロントで新たな計算をしない — 一致度はmatch_level・残時間は
// response_deadlineの書式変換のみ・visibility分岐はproposalの
// time_summaryキー有無(生成側の2形と1:1)。
import { formatCategory } from "../intent/format.js";
import {
  ANSWERED_TEXT,
  CLOSED_TEXT,
  DEADLINE_CLOSED_TEXT,
  DEADLINE_SOON_TEXT,
  MATCH_LEVEL_TEXT,
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
  ];
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
