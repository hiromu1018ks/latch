// 通報flow(M3 ws-7 design §2.7・08 §5.2)。提案詳細と成立済み詳細の両画面
// から開く。成立済み=reportee_id明示(1対1は相手固定・グループは選択)、
// 提案1対1=reportee_id省略body(案X: サーバがlatch参加者から解決)。
import {
  RATE_LIMIT_TEXT,
  REPORT_REASON_OPTIONS,
  REPORT_TOAST_TEXT,
} from "./texts.js";
import { escapeHtml } from "./view.js";

export const createReportFlow = ({ client, chrome, modal }) => {
  let latchId = null;
  let fixedReporteeId = null;
  let fixedName = "";
  let candidates = []; // [{id, name}] — グループ選択肢
  let omitReportee = false;

  const render = () => {
    const targetHtml = fixedReporteeId
      ? `<p class="report-target">通報対象: <strong>${escapeHtml(fixedName)}</strong></p>`
      : candidates.length > 0
        ? `<fieldset class="report-target-select"><legend>通報する相手</legend>${candidates
            .map(
              (c) =>
                `<label><input type="radio" name="reportee" value="${c.id}" /> ${escapeHtml(c.name)}</label>`,
            )
            .join("")}</fieldset>`
        : ""; // 提案1対1: 対象表示なし(非開示のため)
    modal.innerHTML = `
      <section class="confirmation report-dialog" role="dialog" aria-modal="true" aria-labelledby="reportTitle">
        <button type="button" class="modal-close" data-role="report-close" aria-label="閉じる"><i class="ph ph-x"></i></button>
        <p class="section-kicker centered"><span></span>REPORT</p>
        <h2 id="reportTitle">通報</h2>
        ${targetHtml}
        <fieldset class="report-reasons"><legend>理由</legend>${REPORT_REASON_OPTIONS.map(
          ([code, label]) =>
            `<label><input type="radio" name="reason" value="${code}" /> ${label}</label>`,
        ).join("")}</fieldset>
        <p class="report-error" data-role="report-error" role="alert" hidden></p>
        <button type="button" class="report-submit" data-role="report-submit">通報する</button>
      </section>
    `;
    modal.querySelector("[data-role=report-close]").addEventListener("click", close);
    modal.querySelector("[data-role=report-submit]").addEventListener("click", submit);
  };

  const showError = (text) => {
    const errorEl = modal.querySelector("[data-role=report-error]");
    errorEl.textContent = text;
    errorEl.hidden = false;
  };

  const open = ({ latchId: id, participants = null, meId = null }) => {
    latchId = id;
    fixedReporteeId = null;
    fixedName = "";
    candidates = [];
    omitReportee = participants == null;
    if (participants) {
      const others = participants.filter((p) => p.user_id !== meId);
      if (others.length === 1) {
        fixedReporteeId = others[0].user_id; // 1対1: 相手固定
        fixedName = others[0].display_name;
      } else {
        candidates = others.map((p) => ({ id: p.user_id, name: p.display_name }));
      }
    }
    render();
    modal.hidden = false;
    document.body.classList.add("modal-open");
  };

  const close = () => {
    modal.hidden = true;
    document.body.classList.remove("modal-open");
  };

  const submit = async () => {
    const reason = modal.querySelector('input[name="reason"]:checked')?.value;
    if (!reason) {
      showError("理由を選んでください。");
      return;
    }
    const selected = modal.querySelector('input[name="reportee"]:checked')?.value;
    const body = { latch_id: latchId, reason };
    if (fixedReporteeId) body.reportee_id = fixedReporteeId;
    else if (selected) body.reportee_id = selected;
    // omitReportee(提案1対1)はreportee_idなしのまま送る(案X)
    try {
      await client.call("POST", "/v1/reports", { body });
      close();
      chrome.showToast(REPORT_TOAST_TEXT); // 受付・記録のみ(08 §5.2)
    } catch (err) {
      showError(err?.code === "RATE_LIMITED" ? RATE_LIMIT_TEXT : "送信できませんでした。");
    }
  };

  // backdropクリックで閉じる(confirmationModalと同型・一度だけwire)
  modal.addEventListener("click", (event) => {
    if (event.target === modal && !modal.hidden) close();
  });

  return { open, submit, close };
};
