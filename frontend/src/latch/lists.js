// 候補・成立の簡易一覧(UI v2 Phase 5・phase0-decisions D-11の最小版)。
// topnav/tabbarの「候補」「成立」タブの一覧画面。様式は03のリスト部品
// (sb-row: 44pxアイコン+原文+meta+pill)の踏襲で、行の選択で04/05詳細へ遷移する。
// データ源はGET /v1/latches?limit=20(statusはproposed/partial_accept=候補・
// matched/completed=成立に分類)。見出しは承認済みの通知文言
// (PROPOSAL_NOTICE_TITLE・MATCHED_NOTICE_TITLE)を流用する。
import {
  HIDDEN_PROPOSAL_TEXT,
  LIST_MATCHED_BADGE,
  LIST_PROPOSALS_BADGE,
  MATCHED_NOTICE_TITLE,
  NEXT_ACTION_TEXT,
  NO_LATCH_NOTE,
  NO_LATCH_TITLE,
  NO_MATCHED_NOTE,
  NO_MATCHED_TITLE,
  PROPOSAL_NOTICE_TITLE,
  STATUS_TEXT,
} from "./texts.js";
import { escapeHtml, isMinimalProposal, remainingTimeText } from "./view.js";
import { iconHtml } from "../ui/icons.js";
import { splitStatuses, categoryIconName } from "./home.js";

const MODES = {
  proposals: {
    badge: LIST_PROPOSALS_BADGE,
    title: PROPOSAL_NOTICE_TITLE,
    empty: [NO_LATCH_TITLE, NO_LATCH_NOTE],
  },
  matched: {
    badge: LIST_MATCHED_BADGE,
    title: MATCHED_NOTICE_TITLE,
    empty: [NO_MATCHED_TITLE, NO_MATCHED_NOTE],
  },
};

// 一覧行(候補=原文または「条件が合う候補があります」+残時間・成立=内容+導線文言)
export const latchRowHtml = (latch, nowIso, { first = false } = {}) => {
  const minimal = isMinimalProposal(latch);
  const p = latch.proposal ?? {};
  const title = minimal
    ? HIDDEN_PROPOSAL_TEXT
    : [p.time_summary, p.area_name].filter(Boolean).join(" ");
  const isCandidate = ["proposed", "partial_accept"].includes(latch.status);
  const meta = [
    !minimal && p.headcount != null ? `${p.headcount}人` : null,
    isCandidate
      ? remainingTimeText(latch.response_deadline, nowIso) // 最小形も残時間は出す(03 §4の様式)
      : NEXT_ACTION_TEXT,
  ].filter(Boolean);
  const status = STATUS_TEXT[latch.status] ?? latch.status;
  // 候補=acc(navバッジと同じ強調)・完了=s2のgone様式
  const pillClass = latch.status === "completed" ? "gone" : "acc";
  return `
    <li class="sb-row"${first ? "" : ' data-border="true"'}>
      <span class="sb-row-ic" aria-hidden="true">${iconHtml(categoryIconName(p.category_primary), 19)}</span>
      <a class="sb-row-body sb-row-link" href="#/latches/${latch.id}">
        <span class="sb-row-text">${minimal ? escapeHtml(title) : `「${escapeHtml(title)}」`}</span>
        ${meta.length ? `<span class="sb-row-meta">${meta.map(escapeHtml).join(" · ")}</span>` : ""}
      </a>
      <span class="sb-pill ${pillClass}">${escapeHtml(status)}</span>
    </li>`;
};

export const createLatchList = ({ client, root, mode, onCounts }) => {
  const conf = MODES[mode];
  let items = [];
  let cursor = null;
  let latchItems = []; // 全status(バッジ集計用・splitStatusesに渡す)

  const render = () => {
    const nowIso = new Date().toISOString();
    root.querySelector("[data-list-badge-text]").textContent =
      `${conf.badge} · ${items.length}件`;
    root.querySelector("[data-list-badge]").hidden = false;
    root.querySelector("[data-list-title]").textContent = conf.title;
    root.querySelector("[data-list-rows]").innerHTML = items.length
      ? items
          .map((latch, i) => latchRowHtml(latch, nowIso, { first: i === 0 }))
          .join("")
      : `<li class="latch-empty list-empty"><strong>${conf.empty[0]}</strong><span>${conf.empty[1]}</span></li>`;
    root.querySelector("[data-list-more]").hidden = cursor == null;
  };

  const apply = (data) => {
    latchItems = latchItems.concat(data.items); // 追頁は累積(notice・blocksと同型)
    cursor = data.next_cursor;
    const { candidates, matched } = splitStatuses(latchItems);
    items = mode === "proposals" ? candidates : matched;
    onCounts?.(
      mode === "proposals"
        ? { proposals: candidates.length }
        : { matched: matched.length },
    );
    render();
  };

  const show = async () => {
    apply(await client.call("GET", "/v1/latches?limit=20"));
  };

  const loadMore = async () => {
    if (cursor == null) return;
    apply(
      await client.call(
        "GET",
        `/v1/latches?limit=20&cursor=${encodeURIComponent(cursor)}`,
      ),
    );
  };

  return { show, loadMore };
};
