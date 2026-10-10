// ホーム(M3 ws-7 design §2.2・UI v2 Phase 1)。入力画面=ホーム — workspaceの
// 下段へLATCH候補・成立済みLATCHの2セクション(暫定表示・Phase 3/4で一覧画面へ)。
// GET /v1/latches(1回)をクライアント側でstatus仕分け(終了済みは表示しない・引用#9)。
// 旧topbar「Intent N件」popoverはv2シェルで廃止 — 件数はonCounts経由で
// ヘッダー/タブバーのバッジ(待機中・候補・成立)へ供給する。一覧本体は
// Phase 3(03待機中)で再構成。
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

export const createHome = ({ client, el, onCounts }) => {
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
    onCounts?.({ proposals: candidates.length, matched: matched.length });
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
    // active数=待機中バッジの供給源(一覧表示はPhase 3の03画面で復活)
    const data = await client.call("GET", "/v1/intents?status=active");
    onCounts?.({ standby: data.items.length });
  };

  return { load, loadMore, loadIntents };
};
