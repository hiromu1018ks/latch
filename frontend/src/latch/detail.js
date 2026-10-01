// LATCH詳細(M3 ws-7 design §2.3)。1画面3姿 — statusでproposal/matched/
// closedへ分岐(分岐の入力は常にサーバのstatus・API応答で再描画)。
// ページ内で状態が変わる場面(回答・成立・締切)はすべて再取得で収束する。
import { createAttendanceFlow } from "./attendance.js";
import { createChat } from "./chat.js";
import { createRespondFlow } from "./respond.js";
import {
  ANSWERED_TEXT,
  HIDDEN_PROPOSAL_TEXT,
  HOME_LINK_TEXT,
  LATCH_NOT_FOUND_TEXT,
  NEXT_ACTION_TEXT,
  RESPONSE_BUTTONS,
} from "./texts.js";
import {
  closedText,
  conditionSummaryLines,
  escapeHtml,
  groupNeedText,
  latchMode,
  matchLevelText,
  remainingMs,
  remainingTimeText,
} from "./view.js";

const DEADLINE_TICK_MS = 30_000;

export const createDetail = ({ client, appState, chrome, root, reportFlow }) => {
  let latchId = null;
  let timer = null;
  let chat = null;

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

  const startDeadlineTimer = (latch) => {
    const deadlineEl = root.querySelector("[data-role=deadline]");
    if (!deadlineEl) return;
    const update = () => {
      const nowIso = new Date().toISOString();
      deadlineEl.textContent = remainingTimeText(latch.response_deadline, nowIso);
      if (remainingMs(latch.response_deadline, nowIso) <= 0) {
        // 残時間0で回答ボタンをdisabled(送信可否の真実はサーバの409)
        for (const button of root.querySelectorAll(".respond-button")) {
          button.disabled = true;
        }
      }
    };
    update();
    timer = setInterval(update, DEADLINE_TICK_MS);
  };

  // 回答済み表示の現況行(design §2.4): yes済みでまだ成立していなければ
  // 「あとN人の回答が必要」— この行はstatus(partial_accept)条件で、
  // 未回答時のグループ現況行(view.jsのgroupNeedText・is_group条件)とは
  // 別の規定(1対1のpartial_accept=相手の回答待ちでも表示)。
  const answeredNeedText = (latch) =>
    latch.status === "partial_accept" && latch.remaining_responses > 0
      ? `あと${latch.remaining_responses}人の回答が必要`
      : null;

  const renderProposal = (latch) => {
    const section = document.createElement("section");
    section.className = "latch-detail";
    const summaryLines = conditionSummaryLines(latch);
    // visibility分岐: 最小形は条件サマリ欄を出さず本文を置く(引用#5)
    const summaryHtml = summaryLines
      ? `<p class="latch-summary">${summaryLines.map(escapeHtml).join("<br>")}</p>`
      : `<p class="latch-summary latch-summary-hidden">${HIDDEN_PROPOSAL_TEXT}</p>`;
    const need = groupNeedText(latch);
    const metaHtml = [
      `<span class="badge">一致度 ${matchLevelText(latch)}</span>`,
      `<span class="latch-deadline" data-role="deadline"></span>`,
      need ? `<span class="badge">${escapeHtml(need)}</span>` : "",
    ].join("");
    // 回答済みなら3択を差し替え(design §2.4)
    const answeredNeed = answeredNeedText(latch);
    const actionsHtml = latch.my_response
      ? `<p class="answered-text">${ANSWERED_TEXT[latch.my_response]}</p>${
          latch.my_response === "yes" && answeredNeed
            ? `<p class="group-note">${escapeHtml(answeredNeed)}</p>`
            : ""
        }`
      : `<div class="respond-actions">${RESPONSE_BUTTONS.map(
          ([value, label]) =>
            `<button type="button" class="respond-button" data-value="${value}">${label}</button>`,
        ).join("")}</div>`;
    const entryLabel = reportEntryHtml("proposal", latch.is_group);
    section.innerHTML = `
      <p class="section-kicker"><span></span>LATCH</p>
      ${summaryHtml}
      <p class="latch-meta">${metaHtml}</p>
      ${actionsHtml}
      ${entryLabel ? `<button type="button" class="report-entry" data-role="report-entry">${entryLabel}</button>` : ""}
    `;
    root.replaceChildren(section);
    const respondFlow = createRespondFlow({
      client,
      latchId: latch.id,
      refresh,
      showError,
    });
    const buttons = [...section.querySelectorAll(".respond-button")];
    for (const button of buttons) {
      button.addEventListener("click", () =>
        respondFlow.submit(button.dataset.value, buttons),
      );
    }
    startDeadlineTimer(latch);
    wireReportEntry(section, latch, null);
  };

  const renderMatched = async (latch) => {
    const me = await appState.ensureMe();
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
  };

  const renderClosed = (latch) => {
    const section = document.createElement("section");
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
    const section = document.createElement("section");
    section.className = "latch-detail latch-notfound";
    section.innerHTML = `<p class="notfound-text">${LATCH_NOT_FOUND_TEXT}</p>
      <a class="home-link" href="#/">${HOME_LINK_TEXT}</a>`;
    root.replaceChildren(section);
  };

  const render = async (latch) => {
    stopTimers();
    const mode = latchMode(latch);
    if (mode === "proposal") renderProposal(latch);
    else if (mode === "matched") await renderMatched(latch);
    else renderClosed(latch);
  };

  const show = async (id) => {
    latchId = id;
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

  return { show, refresh };
};
