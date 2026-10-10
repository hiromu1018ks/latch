// 03「待機中」の配線(UI v2 Phase 3・プロトタイプ03が正源)。
// 着地状態(?deposited=1)と通常状態の2姿・メイン条件カード(pill・原文引用・
// 条件タグ・残時間タイマー1秒更新・条件を直す・取り下げる)・取り下げdialog
// (chrome.openAppDialog=<dialog class="dlg">・取り下げ済み状態への切替)・
// オーブと「いま、LATCHがしていること」3カード・ほかに待機中の意思リスト
// (直す/取り下げる)・右上トースト(候補が届いたら「見る」→04)。
// データ源はGET /v1/intents?status=active(created_at降順=先頭がメイン)。
// 取り下げ=DELETE /v1/intents/{id}(05 §5)。条件を直す=02編集モード
// (#/confirm?intent={id}・保存はPATCH /v1/intents/{id})。
// raw_textは所有者への表示のみに使い、ログへ出さない(01 §21)。
import {
  conditionTags,
  depositedAgoText,
  expiryLine,
  formatCategoryPrimary,
  jstParts,
} from "../intent/format.js";
import { escapeHtml, hhmmssText } from "./view.js";
import {
  CANDIDATE_TOAST_TEXT,
  DEPOSIT_AGAIN_LABEL,
  EDIT_CONDITION_LABEL,
  NO_OTHERS_TEXT,
  STANDBY_DEPOSITED_A,
  STANDBY_DEPOSITED_B,
  STANDBY_DEPOSITED_LEAD,
  STANDBY_LEAD,
  STANDBY_PILL_TEXT,
  STANDBY_TITLE_A,
  STANDBY_TITLE_B,
  WITHDRAW_BUTTON_LABEL,
  WITHDRAW_CANCEL_LABEL,
  WITHDRAW_DIALOG_NOTE,
  WITHDRAW_DIALOG_TITLE,
  WITHDRAWN_NOTE_TEXT,
  WITHDRAWN_PILL_TEXT,
  WITHDRAWN_TOAST_TEXT,
} from "./texts.js";
import { iconHtml } from "../ui/icons.js";
import { splitStatuses, categoryIconName } from "./home.js";

const $ = (selector, root = document) => root.querySelector(selector);
const TIMER_TICK_MS = 1000;

// オーブの浮遊チップ(プロトタイプ03の絶対位置。time/予算はIBM Plex Mono)
const ORB_CHIP_POSITIONS = [
  { pos: "top:9%;left:2%", mono: true },
  { pos: "top:15%;right:0", mono: false },
  { pos: "bottom:14%;left:0", mono: false },
  { pos: "bottom:7%;right:6%", mono: true },
];

export const mainCardHtml = (intent, nowIso) => {
  const structured = intent.structured_intent ?? {};
  const category = formatCategoryPrimary(structured.category);
  const ago = depositedAgoText(intent.created_at, nowIso);
  const tags = conditionTags(structured)
    .map((tag) => `<span class="sb-tag">${escapeHtml(tag)}</span>`)
    .join("");
  const clock = intent.expires_at ? jstParts(intent.expires_at) : null;
  const timerLabel = clock
    ? `${clock.hour}:${clock.minute} の取り下げまで`
    : "期限なし";
  return `
    <div class="sb-main-head">
      <span class="sb-main-cat">${iconHtml(categoryIconName(structured.category?.primary), 16)}<span>${escapeHtml(category)} · ${escapeHtml(ago)}</span></span>
      <span class="sb-pill stb"><span class="dot" aria-hidden="true"></span>${STANDBY_PILL_TEXT}</span>
    </div>
    <p class="sb-main-quote">「${escapeHtml(intent.raw_text)}」</p>
    ${tags ? `<div class="sb-tags">${tags}</div>` : ""}
    <div class="sb-main-foot">
      <div class="sb-timer">
        <span class="sb-timer-label" id="remain-label">${timerLabel}</span>
        ${clock ? `<span role="timer" aria-labelledby="remain-label" data-sb-timer data-deadline="${escapeHtml(intent.expires_at)}"></span>` : ""}
      </div>
      <div class="sb-main-actions">
        <a class="ghost sb-edit" href="#/confirm?intent=${encodeURIComponent(intent.id)}">${iconHtml("pencil", 15)}${EDIT_CONDITION_LABEL}</a>
        <button type="button" class="ghost danger" data-sb-drop aria-haspopup="dialog">${WITHDRAW_BUTTON_LABEL}</button>
      </div>
    </div>`;
};

// 取り下げ済みのメインカード(削除直後の表示保持・プロトタイプ03のmainGone。
// pill=取り下げ済み・本文t3・フッターは案内と「もう一度預ける」)
export const withdrawnCardHtml = (intent, nowIso) => `
    <div class="sb-main-head">
      <span class="sb-main-cat">${iconHtml(categoryIconName(intent.structured_intent?.category?.primary), 16)}<span>${escapeHtml(formatCategoryPrimary(intent.structured_intent?.category))} · ${escapeHtml(depositedAgoText(intent.created_at, nowIso))}</span></span>
      <span class="sb-pill gone">${WITHDRAWN_PILL_TEXT}</span>
    </div>
    <p class="sb-main-quote gone">「${escapeHtml(intent.raw_text)}」</p>
    <div class="sb-main-foot sb-main-foot--gone">
      <span class="sb-gone-note">${WITHDRAWN_NOTE_TEXT}</span>
      <a class="ghost sb-again" href="#/">${DEPOSIT_AGAIN_LABEL}</a>
    </div>`;

export const orbHtml = (intent) => {
  const chips = conditionTags(intent.structured_intent ?? {})
    .slice(0, ORB_CHIP_POSITIONS.length)
    .map((tag, i) => {
      const { pos, mono } = ORB_CHIP_POSITIONS[i];
      return `<span class="orb-chip"${mono ? ' data-mono' : ""} style="${pos}">${escapeHtml(tag)}</span>`;
    });
  return `
    <div class="orb-haze" aria-hidden="true"></div>
    <div class="orb-ring" aria-hidden="true"></div>
    <div class="orb-ring2" aria-hidden="true"></div>
    <div class="orb-core" aria-hidden="true"></div>
    <div class="ripple" aria-hidden="true"></div>
    <div class="ripple d2" aria-hidden="true"></div>
    <div class="ripple d3" aria-hidden="true"></div>
    <svg viewBox="0 0 200 200" fill="none" class="orb-svg" aria-hidden="true">
      <circle cx="100" cy="100" r="74" stroke="#3A4260" stroke-width="1" stroke-dasharray="1.5 7"></circle>
      <g class="arcL"><path d="M100 70 A30 30 0 0 0 100 130" style="stroke: var(--stb)" stroke-width="7" stroke-linecap="round"></path></g>
      <g class="arcR"><path d="M100 70 A30 30 0 0 1 100 130" style="stroke: var(--t1)" stroke-width="7" stroke-linecap="round"></path></g>
    </svg>
    ${chips.join("")}`;
};

export const othersRowHtml = (intent, nowIso, { first = false } = {}) => {
  const structured = intent.structured_intent ?? {};
  const meta = [
    formatCategoryPrimary(structured.category),
    depositedAgoText(intent.created_at, nowIso),
    expiryLine(intent.expires_at, nowIso),
  ]
    .map(escapeHtml)
    .join(" · ");
  const label = escapeHtml(intent.raw_text);
  return `
    <li class="sb-row"${first ? "" : ' data-border="true"'}>
      <span class="sb-row-ic" aria-hidden="true">${iconHtml(categoryIconName(structured.category?.primary), 19)}</span>
      <div class="sb-row-body">
        <span class="sb-row-text">「${label}」</span>
        <span class="sb-row-meta">${meta}</span>
      </div>
      <div class="sb-row-actions">
        <span class="sb-pill stb"><span class="dot" aria-hidden="true"></span>${STANDBY_PILL_TEXT}</span>
        <a class="ghost sb-row-edit" href="#/confirm?intent=${encodeURIComponent(intent.id)}" aria-label="${label}を直す">直す</a>
        <button type="button" class="ghost danger" data-sb-drop="${intent.id}" aria-haspopup="dialog" aria-label="${label}を取り下げる">${WITHDRAW_BUTTON_LABEL}</button>
      </div>
    </li>`;
};

export const createStandby = ({ client, chrome, root, onCounts }) => {
  let items = []; // active intents(created_at降順)
  let goneMain = null; // 取り下げ直後のメインカードの表示保持
  let timer = null;
  const toastShown = new Set(); // 候補トースト表示済みlatch id(セッション内1回)

  const stop = () => {
    if (timer) window.clearInterval(timer);
    timer = null;
  };

  const mainIntent = () => items[0] ?? null;
  const otherIntents = () => items.slice(1);

  // --- 残時間タイマー(role=timer・1秒更新・HH:MM:SS) --------------------------
  const tickTimer = () => {
    const el = $("[data-sb-timer]", root);
    if (!el) return;
    el.textContent = hhmmssText(
      new Date(el.dataset.deadline).getTime() - Date.now(),
    );
  };

  const startTimer = () => {
    stop();
    tickTimer();
    timer = window.setInterval(tickTimer, TIMER_TICK_MS);
  };

  // --- ヒーロー(着地/通常の2姿) ----------------------------------------------
  const isDeposited = () =>
    new URLSearchParams(window.location.hash.split("?")[1] ?? "").get(
      "deposited",
    ) === "1";

  const render = () => {
    const deposited = isDeposited();
    $("[data-sb-title-a]", root).textContent = deposited
      ? STANDBY_DEPOSITED_A
      : STANDBY_TITLE_A;
    $("[data-sb-title-b]", root).textContent = deposited
      ? STANDBY_DEPOSITED_B
      : STANDBY_TITLE_B;
    $("[data-sb-lead]", root).textContent = deposited
      ? STANDBY_DEPOSITED_LEAD
      : STANDBY_LEAD;
    $("[data-sb-badge]", root).textContent = `待機中 · ${items.length}件`;

    const mainEl = $("[data-sb-main]", root);
    const orbEl = $("[data-sb-orb]", root);
    const nowIso = new Date().toISOString();
    if (goneMain) {
      // 取り下げ済み状態への切替(プロトタイプ03のmainGone)
      mainEl.hidden = false;
      mainEl.innerHTML = withdrawnCardHtml(goneMain, nowIso);
      mainEl.removeAttribute("aria-label");
      orbEl.hidden = true;
    } else {
      const main = mainIntent();
      mainEl.hidden = main == null;
      orbEl.hidden = main == null;
      if (main) {
        mainEl.innerHTML = mainCardHtml(main, nowIso);
        mainEl.setAttribute(
          "aria-label",
          `預けた条件（${formatCategoryPrimary(main.structured_intent?.category)}）`,
        );
        orbEl.innerHTML = orbHtml(main);
        startTimer();
      } else {
        mainEl.innerHTML = "";
        orbEl.innerHTML = "";
        stop();
      }
    }

    const others = otherIntents();
    $("[data-sb-others]", root).innerHTML = others.length
      ? others
          .map((intent, i) => othersRowHtml(intent, nowIso, { first: i === 0 }))
          .join("")
      : `<li class="sb-row-empty">${NO_OTHERS_TEXT}</li>`;
    onCounts?.({ standby: items.length });
  };

  // --- 取り下げ(<dialog class="dlg">→DELETE /v1/intents/{id}) -----------------
  let dropTarget = null;

  const askDrop = (intent) => {
    dropTarget = intent;
    const quote = document.createElement("p");
    quote.className = "dlg-quote";
    quote.textContent = `「${intent.raw_text}」`; // textContent代入(XSS対策)
    const desc = document.createElement("p");
    desc.className = "dlg-desc";
    desc.textContent = WITHDRAW_DIALOG_NOTE;
    const wrap = document.createElement("div");
    wrap.append(quote, desc);
    chrome.openAppDialog({
      title: WITHDRAW_DIALOG_TITLE,
      body: wrap,
      actions: [
        { label: WITHDRAW_CANCEL_LABEL, variant: "ghost" },
        {
          label: WITHDRAW_BUTTON_LABEL,
          variant: "danger",
          onClick: () => withdraw(dropTarget),
        },
      ],
    });
  };

  const withdraw = async (intent) => {
    if (!intent) return;
    const wasMain = mainIntent()?.id === intent.id;
    try {
      await client.call("DELETE", `/v1/intents/${intent.id}`);
      items = items.filter((item) => item.id !== intent.id);
      if (wasMain) goneMain = intent; // 取り下げ済みカードを表示し続ける
      chrome.showToast(WITHDRAWN_TOAST_TEXT);
      render();
      await refreshLatches().catch(() => {}); // 候補消滅をバッジへ反映
    } catch {
      chrome.showToast("取り下げできませんでした。もう一度お試しください。");
    }
  };

  // --- 候補トースト(03の右上・見る→04)と候補/成立バッジ ------------------------
  const refreshLatches = async () => {
    const data = await client.call("GET", "/v1/latches?limit=20");
    const { candidates, matched } = splitStatuses(data.items);
    onCounts?.({ proposals: candidates.length, matched: matched.length });
    const newest = candidates[0];
    if (newest && !toastShown.has(newest.id)) {
      toastShown.add(newest.id);
      chrome.showToast({
        message: CANDIDATE_TOAST_TEXT,
        href: `#/latches/${newest.id}`,
      });
    }
  };

  const show = async () => {
    const data = await client.call("GET", "/v1/intents?status=active");
    items = data.items;
    goneMain = null; // 画面再訪ではサーバの真実に揃える(削除済みは存在しない)
    render();
    await refreshLatches().catch(() => {}); // 失敗時はトースト・バッジ随伴なし
  };

  // 取り下げボタンの委譲イベント(メイン=属性なし・ほか=intent id付き)
  root.addEventListener("click", (event) => {
    const button = event.target.closest("[data-sb-drop]");
    if (!button) return;
    const id = button.dataset.sbDrop || mainIntent()?.id;
    const target = items.find((item) => item.id === id);
    if (target) askDrop(target);
  });

  return { show, stop, render };
};
