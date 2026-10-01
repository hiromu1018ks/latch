// ホーム(M3 ws-7 design §2.2)。入力画面=ホーム — workspaceの下段へ
// LATCH候補・成立済みLATCHの2セクション。GET /v1/latches(1回)をクライアント
// 側でstatus仕分け(終了済みは表示しない・引用#9)。topbar「Intent N件」は
// active数を動的化しpopoverに一覧(表示のみ)。
import { formatCategory, formatTime } from "../intent/format.js";
import {
  HIDDEN_PROPOSAL_TEXT,
  NO_LATCH_NOTE,
  NO_LATCH_TITLE,
  NO_MATCHED_NOTE,
  NO_MATCHED_TITLE,
  STATUS_TEXT,
} from "./texts.js";
import {
  conditionSummaryLines,
  escapeHtml,
  matchLevelText,
  remainingTimeText,
} from "./view.js";

const CANDIDATE_STATUSES = new Set(["proposed", "partial_accept"]);
const MATCHED_STATUSES = new Set(["matched", "completed"]);

export const splitStatuses = (items) => ({
  candidates: items.filter((item) => CANDIDATE_STATUSES.has(item.status)),
  matched: items.filter((item) => MATCHED_STATUSES.has(item.status)),
  // rejected/expired/cancelled は捨てる(ホームの一覧から終了済みとして
  // 扱われる — 引用#9。終了済みセクションは設けない)
});

export const latchCardHtml = (latchItem, nowIso) => {
  const lines = conditionSummaryLines(latchItem);
  // 最小形(hidden_until_match)は条件サマリを出さない(引用#5)。見出しは
  // 日時+場所(area_name nullなら日時のみ — 行indexに依存しない)
  const title = lines
    ? [latchItem.proposal.time_summary, latchItem.proposal.area_name]
        .filter(Boolean)
        .join(" ")
    : HIDDEN_PROPOSAL_TEXT;
  const deadline = CANDIDATE_STATUSES.has(latchItem.status)
    ? `<span class="latch-card-deadline">${escapeHtml(
        remainingTimeText(latchItem.response_deadline, nowIso),
      )}</span>`
    : "";
  return `<a class="latch-card" href="#/latches/${latchItem.id}">
    <span class="latch-card-title">${escapeHtml(title)}</span>
    <span class="latch-card-meta">
      <span class="badge">${STATUS_TEXT[latchItem.status] ?? latchItem.status}</span>
      <span class="badge">一致度 ${matchLevelText(latchItem)}</span>
      ${deadline}
    </span>
  </a>`;
};

const emptyHtml = (title, note) =>
  `<p class="latch-empty"><strong>${title}</strong><span>${note}</span></p>`;

const intentRow = (intent) => {
  const row = document.createElement("div");
  row.className = "intent-row";
  const structured = intent.structured_intent ?? {};
  row.innerHTML = `<span class="intent-row-category">${escapeHtml(
    formatCategory(structured.category),
  )}</span>
  <span class="intent-row-time">${escapeHtml(
    formatTime(
      structured.time?.start,
      structured.time?.end,
      new Date().toISOString(),
    ),
  )}</span>`;
  return row;
};

export const createHome = ({ client, el }) => {
  let items = [];
  let cursor = null;

  const render = () => {
    const { candidates, matched } = splitStatuses(items);
    const nowIso = new Date().toISOString();
    el.candidateList.innerHTML = candidates.length
      ? candidates.map((item) => latchCardHtml(item, nowIso)).join("")
      : emptyHtml(NO_LATCH_TITLE, NO_LATCH_NOTE);
    el.matchedList.innerHTML = matched.length
      ? matched.map((item) => latchCardHtml(item, nowIso)).join("")
      : emptyHtml(NO_MATCHED_TITLE, NO_MATCHED_NOTE);
    el.moreButton.hidden = cursor == null; // cursorは不透明文字列(引用#14)
  };

  const load = async () => {
    const data = await client.call("GET", "/v1/latches?limit=20");
    items = data.items;
    cursor = data.next_cursor;
    render();
  };

  const loadMore = async () => {
    if (cursor == null) return;
    const data = await client.call(
      "GET",
      `/v1/latches?limit=20&cursor=${encodeURIComponent(cursor)}`,
    );
    items = items.concat(data.items); // 追加分だけ追加
    cursor = data.next_cursor;
    render();
  };

  const loadIntents = async () => {
    // active一覧(表示)+下書き件数(表示のみ・カウントはactive数)
    const [active, drafts] = await Promise.all([
      client.call("GET", "/v1/intents?status=active"),
      client.call("GET", "/v1/intents?status=draft"),
    ]);
    el.intentCount.innerHTML = `Intent <span>${active.items.length}件</span>`;
    el.intentList.replaceChildren(...active.items.map(intentRow));
    if (drafts.items.length > 0) {
      const note = document.createElement("p");
      note.className = "intent-drafts";
      note.textContent = `下書き ${drafts.items.length}件`;
      el.intentList.append(note);
    }
  };

  return { load, loadMore, loadIntents };
};
