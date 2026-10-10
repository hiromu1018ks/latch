// LATCH詳細 — 04「候補が届く」提案姿の全面再構成(UI v2 Phase 3・プロトタイプ04が
// 正源)+成立姿・不成立(Phase 4で05に再構成)。1画面3姿 — statusで分岐し、
// ページ内で状態が変わる場面(回答・undo・締切)はすべて再取得で収束する
// (表示の唯一の真実は詳細応答・design §2.5)。
// 提案姿: ヒーロー・4情報カード・照合表(自分の条件×候補の対比較)・回答期限
// タイマー(残5分でdng色)・回答2択(D-05)・4状態(open/joined/allIn/declined)・
// undo(D-06)・理由chips(D-07)・目隠しアバター。
// 見送りは「今回は見送る」で即座には送らない — 理由chipsはdefer回答のbodyに
// のみ受理される(05 §5)ため、declined状態の離脱時(「待機中に戻る」・画面遷移)
// に理由といっしょに送る。取り消し(送信前)は画面上の取消のみでAPI呼び出しなし。
import { createAttendanceFlow } from "./attendance.js";
import { createChat } from "./chat.js";
import { createRespondFlow } from "./respond.js";
import {
  ALL_IN_LEAD_TEXT,
  ALL_IN_TITLE,
  BACK_TO_STANDBY_LABEL,
  BLOCK_ENTRY_TEXT,
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
  LATCH_NOT_FOUND_TEXT,
  MINIMAL_PROPOSAL_NOTE,
  NEXT_ACTION_TEXT,
  OPEN_CHAT_LABEL,
  PROPOSAL_LEAD_TEXT,
  REASON_CHIPS,
  REASON_LABEL_TEXT,
  REASON_NOTE_TEXT,
  RESPONSE_BUTTONS,
  RESPONSE_NOTE_TEXT,
  UNDO_BLOCKED_TOAST_TEXT,
  UNDO_DECLINED_LABEL,
  UNDO_JOINED_LABEL,
} from "./texts.js";
import {
  clockText,
  closedText,
  countdownText,
  escapeHtml,
  fitRows,
  formatYen,
  isMinimalProposal,
  joinedWaitText,
  latchMode,
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

export const createDetail = ({ client, appState, chrome, root, reportFlow, blockFlow = null }) => {
  let latchId = null;
  let timer = null;
  let chat = null;
  let respondFlow = null;
  let declinePending = false; // 見送り未送信(離脱時に送る)
  let declineReasons = new Set();
  let showAllInOnce = false; // yes回答で成立した直後=04のallIn状態を見せてから05へ

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

  // -- 通報導線(design §2.7): 成立済み=両画面・提案は1対1のみ --
  const reportEntryHtml = (mode, isGroup) => {
    if (mode === "matched") return "このLATCHを通報する";
    if (mode === "proposal" && !isGroup) return "この提案を通報する";
    return null; // グループ提案: 参加者非開示のため導線なし
  };

  const wireReportEntry = (section, latch, participants) => {
    const entry = section.querySelector("[data-role=report-entry]");
    if (!entry) return;
    entry.addEventListener("click", () => {
      // participants=null(提案1対1)はreportee_id省略body(案X)
      reportFlow.open({
        latchId: latch.id,
        participants: participants ?? null,
        meId: appState.me?.id ?? null,
      });
    });
  };

  // -- ブロック登録導線(ws-8 design §2.7・§5-1承認): 成立済み詳細のみ --
  const wireBlockEntry = (section, participants) => {
    const entry = section.querySelector("[data-role=block-entry]");
    if (!entry) return;
    entry.addEventListener("click", () => {
      blockFlow?.open({ participants, meId: appState.me?.id ?? null });
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
    wireReportEntry(section, latch, null);
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

  // -- 成立姿(05・Phase 4で全面再構成) ------------------------------------------
  const renderMatched = async (latch) => {
    stopTimers();
    const me = await appState.ensureMe();
    root.className = "detail-view";
    const section = document.createElement("section");
    section.className = "latch-detail";
    const participants = latch.participants ?? [];
    const participantsHtml = participants
      .map(
        (p) =>
          `<li class="participant"><strong>${escapeHtml(p.display_name)}</strong>${
            p.profile?.bio ? `<span>${escapeHtml(p.profile.bio)}</span>` : ""
          }</li>`,
      )
      .join("");
    section.innerHTML = `
      <p class="section-kicker"><span></span>LATCH</p>
      <h2 class="latch-heading">集合情報</h2>
      <p class="latch-summary">${escapeHtml(latch.time_summary ?? "")}${
        latch.area_name ? `<br>${escapeHtml(latch.area_name)}` : ""
      }</p>
      <ul class="participants">${participantsHtml}</ul>
      <p class="next-action">${NEXT_ACTION_TEXT}</p>
      <div class="chat-area" data-role="chat"></div>
      ${
        latch.status === "completed"
          ? `<div class="attendance" data-role="attendance"></div>`
          : ""
      }
      <button type="button" class="report-entry" data-role="report-entry">${reportEntryHtml("matched", latch.is_group)}</button>
      <button type="button" class="block-entry" data-role="block-entry">${BLOCK_ENTRY_TEXT}</button>
    `;
    root.replaceChildren(section);
    chat = createChat({
      client,
      latch,
      meId: me.id,
      mount: section.querySelector('[data-role="chat"]'),
    });
    await chat.start();
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
    wireReportEntry(section, latch, participants);
    wireBlockEntry(section, participants);
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
