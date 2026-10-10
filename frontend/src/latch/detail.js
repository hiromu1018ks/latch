// LATCH詳細 — 04「候補が届く」提案姿+05「成立・チャット」の全面再構成(UI v2
// Phase 3/4・プロトタイプ04/05が正源)。1画面3姿 — statusで分岐し、ページ内で
// 状態が変わる場面(回答・undo・締切・成立取消)はすべて再取得で収束する
// (表示の唯一の真実は詳細応答・design §2.5)。
// 提案姿: ヒーロー・4情報カード・照合表(自分の条件×候補の対比較)・回答期限
// タイマー(残5分でdng色)・回答2択(D-05)・4状態(open/joined/allIn/declined)・
// undo(D-06)・理由chips(D-07)・目隠しアバター。
// 見送りは「今回は見送る」で即座には送らない — 理由chipsはdefer回答のbodyに
// のみ受理される(05 §5)ため、declined状態の離脱時(「待機中に戻る」・画面遷移)
// に理由といっしょに送る。取り消し(送信前)は画面上の取消のみでAPI呼び出しなし。
// 成立姿: ヒーロー(snap/burstアニメ・条件タグ)+aside(成立した内容+カレンダーに
// 追加=D-10・メンバー・安全のために)+チャット。成立の取り消し(D-08)は
// POST /v1/latches/{id}/leave — サーバがチャットへシステム行を挿入し、自分は
// 以後書込不可(CHAT_READONLY)。詳細応答にleft_user_idsは出ないため取消後の
// 姿はセッション内のフラグで保持する(ALREADY_LEFTでも同様に切替える)。
import { createAttendanceFlow } from "./attendance.js";
import { createChat } from "./chat.js";
import { createRespondFlow } from "./respond.js";
import { calendarButtonHtml, downloadIcs } from "./calendar.js";
import {
  ALL_IN_LEAD_TEXT,
  ALL_IN_TITLE,
  BACK_TO_STANDBY_LABEL,
  CANCEL_DIALOG_CONFIRM,
  CANCEL_DIALOG_DISMISS,
  CANCEL_DIALOG_INTRO,
  CANCEL_DIALOG_NOTE,
  CANCEL_DIALOG_TITLE,
  CANCEL_ENTRY_TEXT,
  DEADLINE_LINE_SUFFIX,
  DECLINED_NOTE_TEXT,
  DECLINED_TITLE,
  FIT_ALL_TEXT,
  FIT_NOTE_TEXT,
  FIT_TITLE,
  FIT_UNAVAILABLE_TEXT,
  HIDDEN_PROPOSAL_TEXT,
  HOME_LINK_TEXT,
  JOINED_TITLE,
  JOINED_WAIT_NOTE,
  LATCHED_DETAILS_TITLE,
  LATCHED_PLACE_SUB_TEXT,
  LATCHED_TITLE,
  LEFT_LEAD_TEXT,
  LEFT_TITLE,
  LATCH_NOT_FOUND_TEXT,
  MEMBER_JOINED_TEXT,
  MEMBER_LEFT_TEXT,
  MEMBER_NOTE_TEXT,
  MINIMAL_PROPOSAL_NOTE,
  OPEN_CHAT_LABEL,
  PROPOSAL_LEAD_TEXT,
  RATE_LIMIT_TEXT,
  REASON_CHIPS,
  REASON_LABEL_TEXT,
  REASON_NOTE_TEXT,
  RESPONSE_BUTTONS,
  RESPONSE_NOTE_TEXT,
  SAFETY_ENTRY_TEXT,
  SAFETY_GUIDE_ITEMS,
  SAFETY_GUIDE_TITLE,
  SAFETY_HEADING_TEXT,
  UNDO_BLOCKED_TOAST_TEXT,
  UNDO_DECLINED_LABEL,
  UNDO_JOINED_LABEL,
} from "./texts.js";
import {
  cancelDialogLines,
  chatMembers,
  clockText,
  closedText,
  countdownText,
  escapeHtml,
  fitRows,
  formatYen,
  isMinimalProposal,
  joinedWaitText,
  latchMode,
  latchedRows,
  latchedTags,
  pickMyIntent,
  proposalStateOf,
  proposalTimeParts,
  remainingMs,
  whoLineText,
} from "./view.js";
import { iconHtml } from "../ui/icons.js";

const DEADLINE_TICK_MS = 1000; // 04のタイマーは分:秒・1秒更新(プロトタイプ04)
const TIMER_LOW_MS = 5 * 60 * 1000; // 残5分でdng色(timer-low)

const $ = (selector, scope = document) => scope.querySelector(selector);

export const createDetail = ({ client, appState, chrome, root, safety }) => {
  let latchId = null;
  let timer = null;
  let chat = null;
  let respondFlow = null;
  let declinePending = false; // 見送り未送信(離脱時に送る)
  let declineReasons = new Set();
  let showAllInOnce = false; // yes回答で成立した直後=04のallIn状態を見せてから05へ
  let matchedAtIso = null; // 成立時刻(yes直後のみ実時刻。APIにmatched_atはない)
  let leftLatch = false; // 参加持取り消し済み(D-08・詳細応答に出ないため画面保持)

  const stopTimers = () => {
    if (timer) clearInterval(timer);
    timer = null;
    chat?.stop();
    chat = null;
  };

  const fetchLatch = () => client.call("GET", `/v1/latches/${latchId}`);

  const refresh = async () => {
    // 回答成功・409系の収束先 — 表示の唯一の真実は詳細応答(design §2.5)
    try {
      const data = await fetchLatch();
      await render(data.latch);
    } catch {
      // 再取得失敗時は現在の表示を維持(次の操作で再試行)
    }
  };

  const showError = (message) => {
    let errorEl = root.querySelector(".detail-error");
    if (!errorEl) {
      errorEl = document.createElement("p");
      errorEl.className = "detail-error";
      errorEl.setAttribute("role", "alert");
      root.append(errorEl);
    }
    errorEl.textContent = message;
    errorEl.hidden = false;
  };

  // -- 見送りの送信(declined状態の離脱時・D-07) --------------------------------
  const sendDecline = async ({ navigate = false } = {}) => {
    if (!declinePending || !respondFlow) return;
    declinePending = false;
    const reasons = [...declineReasons];
    const result = await respondFlow.submit("defer", { reasons });
    if (result.ok) {
      if (navigate) location.hash = "#/standby";
      return;
    }
    if (navigate) {
      // 画面遷移の解除では画面が出ていないためトーストで伝える
      chrome.showToast(
        result.kind === "conflict"
          ? "この候補はすでに閉じています"
          : "見送りを送信できませんでした",
      );
    }
  };

  // ルート離脱時にmain.jsから呼ぶ(未送信の見送りを送る+タイマー停止)
  const flush = () => {
    stopTimers();
    if (declinePending) void sendDecline({});
  };

  // -- 通報・ブロック導線(D-09統合dialog): 提案1対1のみ単独導線・成立=aside内 --
  const reportEntryHtml = (mode, isGroup) => {
    if (mode === "matched") return null; // 成立は安全のためにカードへ統合(Phase 4)
    if (mode === "proposal" && !isGroup) return "この提案を通報する";
    return null; // グループ提案: 参加者非開示のため導線なし
  };

  const wireReportEntry = (section, latch) => {
    const entry = section.querySelector("[data-role=report-entry]");
    if (!entry) return;
    entry.addEventListener("click", () => {
      // participants=null(提案1対1)はreportee_id省略body(案X)+ブロック導線なし
      safety.open({
        latchId: latch.id,
        participants: null,
        meId: appState.me?.id ?? null,
      });
    });
  };

  // -- 回答期限タイマー(分:秒・1秒更新・残5分でdng色) --------------------------
  const startDeadlineTimer = (latch) => {
    const deadlineEl = root.querySelector("[data-role=deadline]");
    if (!deadlineEl) return;
    const update = () => {
      const nowIso = new Date().toISOString();
      const ms = remainingMs(latch.response_deadline, nowIso);
      deadlineEl.textContent = countdownText(ms);
      deadlineEl.classList.toggle("timer-low", ms <= TIMER_LOW_MS);
      if (ms <= 0) {
        // 残時間0で回答ボタンをdisabled(送信可否の真実はサーバの409)
        for (const button of root.querySelectorAll("[data-role=respond]")) {
          button.disabled = true;
        }
      }
    };
    update();
    timer = setInterval(update, DEADLINE_TICK_MS);
  };

  // -- オーブ(04・acc色版) -----------------------------------------------------
  const orbChipsHtml = (latch) => {
    const p = latch.proposal ?? {};
    const time = proposalTimeParts(p.time_summary);
    const chips = [
      { pos: "top:9%;left:0", mono: true, text: time?.clock ?? "—" },
      { pos: "top:16%;right:0", mono: false, text: p.area_name ?? "—" },
      { pos: "bottom:15%;left:2%", mono: false, text: p.headcount != null ? `${p.headcount}人` : "—" },
      { pos: "bottom:7%;right:4%", mono: true, text: p.budget?.max != null ? formatYen(p.budget.max) : "—" },
    ];
    return chips
      .map(
        ({ pos, mono, text }) =>
          `<span class="orb-chip bright"${mono ? " data-mono" : ""} style="${pos}">${escapeHtml(text)}</span>`,
      )
      .join("");
  };

  const orbHtml = (latch) => `
    <div class="pv-orb-haze" aria-hidden="true"></div>
    <div class="orb-ring" aria-hidden="true"></div>
    <div class="pv-orb-ring2" aria-hidden="true"></div>
    <div class="pv-orb-core" aria-hidden="true"></div>
    <div class="pripple" aria-hidden="true"></div>
    <div class="pripple d2" aria-hidden="true"></div>
    <div class="pripple d3" aria-hidden="true"></div>
    <svg viewBox="0 0 200 200" fill="none" class="orb-svg" aria-hidden="true">
      <circle cx="100" cy="100" r="74" style="stroke: var(--acc-line)" stroke-width="1" stroke-dasharray="1.5 7"></circle>
      <g class="parcL"><path d="M100 70 A30 30 0 0 0 100 130" style="stroke: var(--acc)" stroke-width="7" stroke-linecap="round"></path></g>
      <g class="parcR"><path d="M100 70 A30 30 0 0 1 100 130" style="stroke: var(--t1)" stroke-width="7" stroke-linecap="round"></path></g>
    </svg>
    ${orbChipsHtml(latch)}`;

  // -- 4情報カード(全フィールド版のみ) ----------------------------------------
  const infoCardHtml = ({ label, value, mono = false, sub }) => `
    <div class="pv-card">
      <span class="pv-card-label">${escapeHtml(label)}</span>
      <span class="pv-card-value${mono ? " mono" : ""}">${escapeHtml(value)}</span>
      <span class="pv-card-sub">${escapeHtml(sub)}</span>
    </div>`;

  const infoCardsHtml = (latch) => {
    const p = latch.proposal ?? {};
    const time = proposalTimeParts(p.time_summary);
    const headcount = p.headcount ?? 2;
    return [
      infoCardHtml({
        label: "開始",
        value: time?.clock ?? "—",
        mono: true,
        sub: time?.date ?? "成立後にわかります",
      }),
      infoCardHtml({
        label: "エリア",
        value: p.area_name ?? "—",
        sub: p.area_name ? "お店は成立後に相談" : "成立後にわかります",
      }),
      infoCardHtml({
        label: "人数",
        value: `${headcount}人`,
        sub: `あなた＋${headcount - 1}人`,
      }),
      infoCardHtml({
        label: "予算",
        value: p.budget?.max != null ? formatYen(p.budget.max) : "—",
        mono: p.budget?.max != null,
        sub: p.budget?.max != null ? "1人あたり · 前後" : "成立後にわかります",
      }),
    ].join("");
  };

  // -- 照合表(自分の条件×候補の対比較) ----------------------------------------
  const fitHtml = (myIntent, latch, nowIso) => {
    const rows = fitRows(myIntent, latch, nowIso)
      .map(
        (row) => `
      <li class="fit-row">
        <span class="fit-label">${escapeHtml(row.label)}</span>
        <span class="fit-mine"><span class="vh">あなたの条件：</span>${escapeHtml(row.mine)}</span>
        ${iconHtml("arrowRight", 16)}
        <span class="fit-theirs"><span class="vh">候補：</span>${escapeHtml(row.theirs)}</span>
        <span class="fit-ok" ${row.ok ? "" : "hidden"}>${iconHtml("check", 14)}</span>
      </li>`,
      )
      .join("");
    const allOk = myIntent != null;
    return `
      <div class="pv-fit-head">
        <h2 id="h-fit" class="card-title">${FIT_TITLE}</h2>
        <span class="pv-fit-sum">${
          allOk
            ? `${iconHtml("check", 14)}${FIT_ALL_TEXT}`
            : FIT_UNAVAILABLE_TEXT
        }</span>
      </div>
      <p class="vh">左があなたの条件、右がこの候補の内容です。</p>
      <ul class="pv-fit-list">${rows}</ul>
      <p class="pv-fit-note">${FIT_NOTE_TEXT}</p>`;
  };

  // -- 目隠しアバタースタック(自分=イニシャル・相手=点線円+目隠し) --------------
  const avatarsHtml = (latch, meInitial) => {
    const total = latch.proposal?.headcount ?? 2;
    const state = proposalStateOf(latch);
    const ring = state === "joined" || state === "allIn" ? "var(--acc)" : "var(--line-strong)";
    const blinds = Math.max(0, total - 1);
    const blind = `<span class="pv-avatar blind">${iconHtml("eyeOff", 18)}</span>`;
    return `
      <div aria-hidden="true" class="pv-avatar-stack">
        <span class="pv-avatar me" style="border-color: ${ring}">${escapeHtml(meInitial)}</span>
        ${blind.repeat(blinds)}
      </div>`;
  };

  // -- 4状態の回答カード --------------------------------------------------------
  const reasonChipsHtml = () =>
    REASON_CHIPS.map(
      ([value, label]) =>
        `<button type="button" class="ghost reason-chip" data-reason="${value}" aria-pressed="${
          declineReasons.has(value) ? "true" : "false"
        }">${
          declineReasons.has(value) ? iconHtml("check", 14) : ""
        }${escapeHtml(label)}</button>`,
    ).join("");

  const declinedCardHtml = ({ mode }) => `
    <div class="pv-state-card declined">
      <p class="pv-state-title">${DECLINED_TITLE}</p>
      <p class="pv-state-body">${DECLINED_NOTE_TEXT}</p>
      <div role="group" aria-labelledby="reason-label" class="pv-reasons">
        <span id="reason-label" class="pv-reason-label">${REASON_LABEL_TEXT}<span class="pv-reason-sub">${REASON_NOTE_TEXT}</span></span>
        <div class="pv-reason-chips">${reasonChipsHtml()}</div>
      </div>
      <div class="pv-state-actions">
        ${
          mode === "pending"
            ? `<button type="button" class="pv-solid-link" data-role="confirm-decline">${BACK_TO_STANDBY_LABEL}</button>`
            : `<a class="pv-solid-link" href="#/standby">${BACK_TO_STANDBY_LABEL}</a>`
        }
        <button type="button" class="ghost" data-role="${
          mode === "pending" ? "cancel-decline" : "undo"
        }">${UNDO_DECLINED_LABEL}</button>
      </div>
    </div>`;

  const statesHtml = (latch, meInitial) => {
    const state = proposalStateOf(latch);
    if (declinePending && state === "open") {
      // 見送り未送信(理由chipsを選んでから離脱時に送る)
      return declinedCardHtml({ mode: "pending" });
    }
    if (state === "open") {
      return `<div class="pv-state">
        ${RESPONSE_BUTTONS.map(
          ([value, label]) =>
            value === "yes"
              ? `<button type="button" class="cta pv-join" data-role="respond" data-value="${value}">${iconHtml("latchMark", 24)}${label}</button>`
              : `<button type="button" class="ghost pv-defer" data-role="respond" data-value="${value}">${label}</button>`,
        ).join("")}
        <p class="pv-note">${RESPONSE_NOTE_TEXT}</p>
      </div>`;
    }
    if (state === "joined") {
      return `<div class="pv-state-card joined">
        <p class="pv-state-title">${iconHtml("check", 22)}${JOINED_TITLE}</p>
        <p class="pv-state-body">${escapeHtml(joinedWaitText(latch))}</p>
        <p class="pv-state-note">${JOINED_WAIT_NOTE}</p>
        <div class="pv-state-actions">
          <a class="pv-solid-link" href="#/standby">${BACK_TO_STANDBY_LABEL}</a>
          <button type="button" class="ghost" data-role="undo">${UNDO_JOINED_LABEL}</button>
        </div>
      </div>`;
    }
    if (state === "allIn") {
      const total = latch.proposal?.headcount ?? 2;
      return `<div class="pv-state-card allin">
        <p class="pv-state-title">${total > 2 ? `${total}人` : ""}${ALL_IN_TITLE}</p>
        <p class="pv-state-body">${ALL_IN_LEAD_TEXT}</p>
        <button type="button" class="cta pv-chat" data-role="open-chat">${OPEN_CHAT_LABEL}${iconHtml("arrowRight", 18)}</button>
      </div>`;
    }
    // declined(サーバ記録済み=my_response=defer)
    return declinedCardHtml({ mode: "confirmed" });
  };

  // -- 提案姿(04) ---------------------------------------------------------------
  const renderProposal = async (latch) => {
    const minimal = isMinimalProposal(latch);
    declinePending = false; // 描画は常にサーバの真実から(未送信の見送りは離脱で送信済み)
    const nowIso = new Date().toISOString();
    // 照合表の左列(自分の条件)はactive Intentからの値マッチングで特定する
    // (詳細応答にintent_idsがない・gap分析§6。最小形では照合表ごと出さない)
    let myIntent = null;
    if (!minimal) {
      try {
        const data = await client.call("GET", "/v1/intents?status=active");
        myIntent = pickMyIntent(data.items, latch);
      } catch {
        myIntent = null;
      }
    }
    const me = await appState.ensureMe().catch(() => null);
    const meInitial = Array.from(me?.display_name ?? "?")[0] ?? "?";

    root.className = "page-main page-main--proposal";
    const section = document.createElement("section");
    section.className = "pv-detail";
    section.setAttribute("aria-labelledby", "h-proposal");
    section.innerHTML = `
      <div class="haze haze-proposal" aria-hidden="true"></div>
      <div class="pv-grid">
        ${
          minimal
            ? ""
            : `<div aria-hidden="true" class="pv-visual" data-role="pv-orb">${orbHtml(latch)}</div>`
        }
        <div class="pv-text${minimal ? " pv-text--solo" : ""}">
          <p class="pv-badge rise"><span aria-hidden="true" class="pv-badge-dot"></span>LATCH候補 · ${escapeHtml(clockText(latch.created_at))} に届きました</p>
          <h1 id="h-proposal" class="pv-title rise r2">条件が、<br>揃いそうです。</h1>
          <p class="pv-lead rise r3">${PROPOSAL_LEAD_TEXT}</p>
          ${
            minimal
              ? `<div class="pv-minimal rise r3">${iconHtml("info", 18)}<span>${HIDDEN_PROPOSAL_TEXT} — ${MINIMAL_PROPOSAL_NOTE}</span></div>`
              : `<div class="pv-cards rise r3">${infoCardsHtml(latch)}</div>`
          }
        </div>
        ${
          minimal
            ? ""
            : `<section aria-labelledby="h-fit" class="pv-fit card-pad" data-role="pv-fit">${fitHtml(myIntent, latch, nowIso)}</section>`
        }
        <section aria-labelledby="h-decide" class="pv-decide card-pad">
          <div class="pv-decide-head">
            <div class="pv-decide-timer">
              <h2 id="h-decide" class="card-title">回答期限まで</h2>
              <span role="timer" aria-labelledby="h-decide" class="pv-timer" data-role="deadline"></span>
              <span class="pv-deadline-note">${escapeHtml(clockText(latch.response_deadline))}${DEADLINE_LINE_SUFFIX}</span>
            </div>
            <div class="pv-avatars">
              ${avatarsHtml(latch, meInitial)}
              <span class="pv-who" data-role="who"></span>
            </div>
          </div>
          <div aria-live="polite" class="pv-states" data-role="pv-states">${statesHtml(latch, meInitial)}</div>
        </section>
      </div>
      ${
        reportEntryHtml(latchMode(latch), latch.is_group)
          ? `<button type="button" class="report-entry" data-role="report-entry">${reportEntryHtml(latchMode(latch), latch.is_group)}</button>`
          : ""
      }
    `;
    root.replaceChildren(section);
    $("[data-role=who]", section).textContent = whoLineText(
      proposalStateOf(latch),
      latch,
    );

    wireRespond(section, latch);
    startDeadlineTimer(latch);
    wireReportEntry(section, latch);
  };

  // -- 回答・undo・chipsの配線 --------------------------------------------------
  const rerenderStates = (latch, meInitial) => {
    const statesEl = root.querySelector("[data-role=pv-states]");
    if (!statesEl) return;
    statesEl.innerHTML = statesHtml(latch, meInitial);
    const whoEl = root.querySelector("[data-role=who]");
    if (whoEl) whoEl.textContent = whoLineText(proposalStateOf(latch), latch);
    wireRespond(root, latch);
  };

  const wireRespond = (scope, latch) => {
    const meInitial =
      Array.from(appState.me?.display_name ?? "?")[0] ?? "?";

    for (const button of scope.querySelectorAll("[data-role=respond]")) {
      button.addEventListener("click", () => {
        const buttons = [...scope.querySelectorAll("[data-role=respond]")];
        if (button.dataset.value === "yes") {
          // 全員がそろった成立は04のallIn状態(チャットを開く→05)へ一度だけ寄り道する
          showAllInOnce = true;
          matchedAtIso = new Date().toISOString(); // 成立時刻(APIにmatched_atはない)
          void respondFlow.submit("yes", { buttons });
        } else {
          // 見送る=即座には送らない(理由chips→離脱時に送信・D-07)
          declinePending = true;
          declineReasons = new Set();
          rerenderStates(latch, meInitial);
        }
      });
    }

    for (const chip of scope.querySelectorAll(".reason-chip")) {
      chip.addEventListener("click", () => {
        const value = chip.dataset.reason;
        if (declineReasons.has(value)) declineReasons.delete(value);
        else declineReasons.add(value);
        rerenderStates(latch, meInitial);
      });
    }

    scope.querySelector("[data-role=confirm-decline]")?.addEventListener("click", (event) => {
      const button = event.currentTarget;
      button.disabled = true;
      void sendDecline({ navigate: true }).then(() => {
        button.disabled = false;
      });
    });

    scope.querySelector("[data-role=cancel-decline]")?.addEventListener("click", () => {
      declinePending = false; // 送信前の取り消し=画面操作のみ(API呼び出しなし)
      declineReasons = new Set();
      rerenderStates(latch, meInitial);
    });

    for (const undoButton of scope.querySelectorAll("[data-role=undo]")) {
      undoButton.addEventListener("click", async () => {
        const buttons = [...scope.querySelectorAll("[data-role=undo]")];
        const result = await respondFlow.undo(buttons);
        // undo後は再取得でopen姿へ。409(成立済み・期限経過)はトーストで伝える
        if (!result.ok && result.kind === "conflict") {
          chrome.showToast(UNDO_BLOCKED_TOAST_TEXT);
        }
      });
    }

    scope.querySelector("[data-role=open-chat]")?.addEventListener("click", () => {
      void renderMatched(latch);
    });
  };

  // -- 成立姿(05・プロトタイプ05が正源) ------------------------------------------
  // ヒーロー(snap/burstアニメ・条件タグ)+lt-grid(チャット→aside/PC=aside|チャット)
  const heroMarkHtml = () => `
    <div aria-hidden="true" class="lt-mark">
      <div class="lt-burst"></div>
      <div class="lt-mark-glow"></div>
      <svg viewBox="0 0 100 100" fill="none" class="lt-mark-svg">
        <g class="snapL"><path d="M50 22 A28 28 0 0 0 50 78" style="stroke: var(--acc)" stroke-width="7" stroke-linecap="round"></path></g>
        <g class="snapR"><path d="M50 22 A28 28 0 0 1 50 78" style="stroke: var(--t1)" stroke-width="7" stroke-linecap="round"></path></g>
      </svg>
    </div>`;

  const heroHtml = (latch) => {
    const lead = leftLatch
      ? LEFT_LEAD_TEXT
      : `${latch.proposal?.headcount ?? 2}人全員が「参加する」と答えました。ここからは、お互いの姿が見えます。`;
    return `
      ${heroMarkHtml()}
      <div class="lt-hero-text">
        <p class="lt-hero-kicker">成立${
          matchedAtIso ? ` · ${escapeHtml(clockText(matchedAtIso))}` : ""
        }</p>
        <h1 id="h-latched" class="lt-hero-title">${leftLatch ? LEFT_TITLE : LATCHED_TITLE}</h1>
        <p class="lt-hero-lead">${escapeHtml(lead)}</p>
      </div>
      <div class="lt-hero-tags" aria-hidden="true">${latchedTags(latch)
        .map(
          (tag) =>
            `<span class="lt-tag${tag.mono ? " mono" : ""}">${escapeHtml(tag.text)}</span>`,
        )
        .join("")}</div>`;
  };

  // aside 1: 成立した内容(details+dl+カレンダーに追加=D-10)
  const detailsCardHtml = (latch) => {
    const wide = window.matchMedia?.("(min-width: 960px)").matches ?? true;
    return `
      <details class="lt-card lt-details"${wide ? " open" : ""}>
        <summary>
          <h2 class="lt-card-title">${LATCHED_DETAILS_TITLE}</h2>
          ${iconHtml("chevronDown", 18, "chev")}
        </summary>
        <div class="lt-details-body">
          <dl class="lt-dl">${latchedRows(latch)
            .map(
              (row) => `
            <dt>${escapeHtml(row.label)}</dt>
            <dd>${escapeHtml(row.value)}${
              row.sub
                ? `<span class="lt-dd-sub">${LATCHED_PLACE_SUB_TEXT}</span>`
                : ""
            }</dd>`,
            )
            .join("")}</dl>
          ${calendarButtonHtml(latch)}
        </div>
      </details>`;
  };

  // aside 2: メンバー(色付きイニシャルアバター・自分=末尾にあなた行)
  const membersCardHtml = (latch, meId) => `
    <section class="lt-card" aria-labelledby="h-members">
      <h2 id="h-members" class="lt-card-title">メンバー</h2>
      <ul class="lt-members">
        ${chatMembers(latch, meId)
          .map(
            (m) => `
          <li>
            <span aria-hidden="true" class="lt-avatar lt-avatar--member${
              m.me ? " me" : ""
            }"${m.me ? "" : ` style="background:${m.color}"`}>${escapeHtml(m.initial)}</span>
            <span class="lt-member-name">${escapeHtml(m.me ? "あなた" : m.name)}</span>
            ${
              m.me && leftLatch
                ? `<span class="lt-member-status left">${MEMBER_LEFT_TEXT}</span>`
                : `<span class="lt-member-status">${iconHtml("check", 13)}${MEMBER_JOINED_TEXT}</span>`
            }
          </li>`,
          )
          .join("")}
      </ul>
      <p class="lt-member-note">${MEMBER_NOTE_TEXT}</p>
    </section>`;

  // aside 3: 安全のために(安全ガイド+通報・ブロック=D-09統合dialog)
  const safetyCardHtml = () => `
    <section class="lt-card" aria-labelledby="h-safety">
      <h2 id="h-safety" class="lt-card-title">${iconHtml("shield", 16)}${SAFETY_HEADING_TEXT}</h2>
      <details class="lt-guide">
        <summary>${SAFETY_GUIDE_TITLE}${iconHtml("chevronDown", 16, "chev")}</summary>
        <ul>${SAFETY_GUIDE_ITEMS.map((item) => `<li>${escapeHtml(item)}</li>`).join("")}</ul>
      </details>
      <button type="button" class="ghost danger lt-safety" aria-haspopup="dialog" data-role="safety-entry">
        ${iconHtml("flag", 16)}${SAFETY_ENTRY_TEXT}
      </button>
    </section>`;

  // 成立の取り消し(D-08)dialog — 既存の<dialog class="dlg">#appDialogを使う
  const openCancelDialog = (latch) => {
    const body = document.createElement("div");
    body.className = "dlg-desc";
    const intro = document.createElement("p");
    intro.textContent = CANCEL_DIALOG_INTRO;
    const list = document.createElement("ul");
    list.className = "dlg-list";
    list.innerHTML = cancelDialogLines(latch, appState.me?.id ?? null)
      .map((line) => `<li>${escapeHtml(line)}</li>`)
      .join("");
    const note = document.createElement("p");
    note.className = "dlg-note";
    note.textContent = CANCEL_DIALOG_NOTE;
    body.append(intro, list, note);
    chrome.openAppDialog({
      title: CANCEL_DIALOG_TITLE,
      body,
      actions: [
        { label: CANCEL_DIALOG_DISMISS, variant: "ghost" },
        {
          label: CANCEL_DIALOG_CONFIRM,
          variant: "danger",
          onClick: () => void confirmLeave(),
        },
      ],
    });
  };

  const confirmLeave = async () => {
    try {
      await client.call("POST", `/v1/latches/${latchId}/leave`, { body: {} });
    } catch (err) {
      if (err?.status === 409 && err?.code === "ALREADY_LEFT") {
        leftLatch = true; // 取り消し済み — 取り消し後の姿へ収束させる
        await refresh();
        return;
      }
      chrome.showToast(
        err?.code === "RATE_LIMITED" ? RATE_LIMIT_TEXT : "取り消せませんでした。",
      );
      return;
    }
    leftLatch = true;
    await refresh(); // 再取得で05の取り消し後姿へ(チャット再同期でシステム行も出る)
  };

  const renderMatched = async (latch) => {
    stopTimers();
    const me = await appState.ensureMe().catch(() => null);
    const meId = me?.id ?? null;
    root.className = "page-main page-main--latched";
    const section = document.createElement("section");
    section.className = "lt-detail";
    section.setAttribute("aria-labelledby", "h-latched");
    section.innerHTML = `
      <div class="haze haze-latched" aria-hidden="true"></div>
      <section class="lt-hero">${heroHtml(latch)}</section>
      <div class="lt-grid">
        <section class="lt-chat-slot" aria-labelledby="h-chat" data-role="chat"></section>
        <aside aria-label="成立の詳細" class="lt-aside">
          ${detailsCardHtml(latch)}
          ${membersCardHtml(latch, meId)}
          <div class="lt-attendance" data-role="attendance" hidden></div>
          ${safetyCardHtml()}
          ${
            leftLatch
              ? ""
              : `<button type="button" class="danger lt-cancel" aria-haspopup="dialog" data-role="cancel-entry">${CANCEL_ENTRY_TEXT}</button>`
          }
        </aside>
      </div>`;
    root.replaceChildren(section);

    // チャット(ヘッダー+log+フォームはchat.jsが構成する)
    chat = createChat({ client, latch, meId, mount: section.querySelector('[data-role="chat"]') });
    if (leftLatch) chat.markLeft(); // 取り消し後の再描画でも書込不可を維持(D-08)
    await chat.start();

    // aside内の導線
    section
      .querySelector("[data-role=safety-entry]")
      ?.addEventListener("click", () => {
        safety.open({
          latchId: latch.id,
          participants: latch.participants ?? [],
          meId,
        });
      });
    section
      .querySelector("[data-role=cancel-entry]")
      ?.addEventListener("click", () => openCancelDialog(latch));
    section.querySelector("[data-role=calendar-add]")?.addEventListener("click", () => {
      downloadIcs(latch);
    });

    if (latch.status === "completed") {
      // 表示条件はstatusのみ(詳細応答のcompleted_atは常にnull・§9-6①。
      // 3日窓・二重回答はサーバの409で切替える)
      const attendanceFlow = createAttendanceFlow({
        client,
        latchId: latch.id,
        mount: section.querySelector('[data-role="attendance"]'),
      });
      attendanceFlow.renderQuestion();
    }
  };

  // -- 不成立(自分の見送り=declined状態カード・それ以外は統一文言) --------------
  const renderClosed = (latch) => {
    stopTimers();
    root.className = "detail-view";
    const section = document.createElement("section");
    if (latch.my_response === "defer") {
      // 自分の見送り=04のdeclined状態(理由chipsは表示のみ・回答時のみ受理)。
      // 取り消し(undo)で未回答へ戻せる(D-06)
      section.className = "latch-detail latch-declined";
      section.innerHTML = declinedCardHtml({ mode: "confirmed" });
      root.replaceChildren(section);
      wireRespond(section, latch);
      return;
    }
    section.className = "latch-detail latch-closed";
    // 二値化はview.jsの純関数に一元化(自分の操作履歴/統一文言・03 §7)
    section.innerHTML = `
      <p class="section-kicker"><span></span>LATCH</p>
      <p class="closed-text">${escapeHtml(closedText(latch))}</p>
    `;
    root.replaceChildren(section);
  };

  const renderNotFound = () => {
    stopTimers();
    root.className = "detail-view";
    const section = document.createElement("section");
    section.className = "latch-detail latch-notfound";
    section.innerHTML = `<p class="notfound-text">${LATCH_NOT_FOUND_TEXT}</p>
      <a class="home-link" href="#/">${HOME_LINK_TEXT}</a>`;
    root.replaceChildren(section);
  };

  const render = async (latch) => {
    stopTimers();
    // chipsの選択は送信時に消費する。描画は常にサーバの真実から組み直す
    declineReasons = new Set();
    respondFlow = createRespondFlow({
      client,
      latchId: latch.id,
      refresh,
      showError,
    });
    const mode = latchMode(latch);
    // yes回答で成立した直後は04のallIn状態(チャットを開く)を一度だけ出す。
    // 直接訪問・再読み込みは05(チャット)へ
    if (mode === "proposal" || (mode === "matched" && showAllInOnce)) {
      await renderProposal(latch);
    } else if (mode === "matched") {
      await renderMatched(latch);
    } else {
      renderClosed(latch);
    }
  };

  const show = async (id) => {
    latchId = id;
    showAllInOnce = false;
    matchedAtIso = null;
    leftLatch = false; // 再訪時はセッションフラグを捨てる(サーバの真実に揃える)
    try {
      const data = await fetchLatch();
      await render(data.latch);
    } catch (err) {
      // 404/403は同一扱い(参加者でない事実の開示を避ける・引用#16)
      if (err?.status === 404 || err?.status === 403) {
        renderNotFound();
        return;
      }
      renderNotFound(); // 通信エラーも導線つきの案内へ(再訪で再試行)
    }
  };

  return { show, refresh, flush };
};
