// 通報・ブロックの統合dialog(UI v2 Phase 4・D-09・プロトタイプ05が正源)。
// 成立済み詳細の「安全のために」ボタンと提案詳細の通報導線から開く。
// 2ステップ: ①対象メンバーのラジオ+「ブロックする」/「通報する」
// ②「通報する」押下時に理由4択のステップをdialog内に挿入する(D-09決定。
// reports APIのreasonは必須のため・プロトタイプは作り込みの省略)。
// 完了はdialog内のrole=status表示(プロトタイプのdone状態)+ブロック成功時は
// onBlocked(詳細再取得)で収束する。
import {
  RATE_LIMIT_TEXT,
  REPORT_REASON_OPTIONS,
  SAFETY_BACK_LABEL,
  SAFETY_BLOCK_LABEL,
  SAFETY_CANCEL_LABEL,
  SAFETY_CLOSE_LABEL,
  SAFETY_DIALOG_TITLE,
  SAFETY_ERROR_TEXT,
  SAFETY_NOTE_TEXT,
  SAFETY_REASON_ERROR_TEXT,
  SAFETY_REASON_LEGEND,
  SAFETY_REASON_NOTE,
  SAFETY_REPORT_LABEL,
  SAFETY_TARGET_ERROR_TEXT,
  SAFETY_TARGET_LABEL,
} from "./texts.js";
import { escapeHtml } from "./view.js";

// happy-domはラジオの排他(uncheck)を実装しないため、試験からも全解除→選択で
// 扱う(latch-report試験の手法を踏襲)

export const createSafetyFlow = ({ client, dialog, onBlocked }) => {
  let latchId = null;
  let candidates = []; // [{id, name}] — 対象メンバー(自分を除く)
  let fixedId = null; // 1対1: あらかじめ選択済みで出す
  let omitReportee = false; // 提案1対1(案X: 対象はサーバが参加者から解決)
  let selectedId = null;
  let step = "target"; // target | reason | done
  let doneMessage = "";

  const errorHtml = () =>
    `<p class="safety-error" data-role="safety-error" role="alert" hidden></p>`;

  const targetHtml = () => {
    if (omitReportee) return ""; // 提案1対1: 対象表示なし(非開示のため)
    return `
      <fieldset class="safety-targets">
        <legend>${escapeHtml(SAFETY_TARGET_LABEL)}</legend>
        ${candidates
          .map(
            (c) => `
          <label class="safety-opt${fixedId === c.id || selectedId === c.id ? " on" : ""}">
            <input type="radio" name="safety-who" value="${c.id}" ${
              fixedId === c.id || selectedId === c.id ? "checked" : ""
            } /> ${escapeHtml(c.name)}
          </label>`,
          )
          .join("")}
      </fieldset>`;
  };

  const reasonHtml = () => `
    <fieldset class="safety-targets safety-reasons">
      <legend>${escapeHtml(SAFETY_REASON_LEGEND)}</legend>
      <span class="safety-reason-note">${escapeHtml(SAFETY_REASON_NOTE)}</span>
      ${REPORT_REASON_OPTIONS.map(
        ([code, label]) => `
        <label class="safety-opt">
          <input type="radio" name="safety-reason" value="${code}" /> ${escapeHtml(label)}
        </label>`,
      ).join("")}
    </fieldset>`;

  const render = () => {
    if (step === "done") {
      dialog.innerHTML = `
        <div class="safety-body">
          <h2>${escapeHtml(SAFETY_DIALOG_TITLE)}</h2>
          <p role="status" class="safety-done">${escapeHtml(doneMessage)}</p>
          <div class="safety-actions">
            <button type="button" class="ghost safety-btn" data-role="safety-close">${escapeHtml(SAFETY_CLOSE_LABEL)}</button>
          </div>
        </div>`;
      dialog.querySelector("[data-role=safety-close]").addEventListener("click", close);
      return;
    }
    if (step === "reason") {
      dialog.innerHTML = `
        <div class="safety-body">
          <h2>${escapeHtml(SAFETY_DIALOG_TITLE)}</h2>
          ${reasonHtml()}
          ${errorHtml()}
          <div class="safety-actions">
            <button type="button" class="ghost safety-btn" data-role="safety-back">${escapeHtml(SAFETY_BACK_LABEL)}</button>
            <button type="button" class="safety-btn safety-solid" data-role="safety-report-submit" disabled>${escapeHtml(SAFETY_REPORT_LABEL)}</button>
          </div>
        </div>`;
      wireReasonStep();
      return;
    }
    // target(1ステップ目)
    dialog.innerHTML = `
      <div class="safety-body">
        <h2>${escapeHtml(SAFETY_DIALOG_TITLE)}</h2>
        ${targetHtml()}
        <p class="safety-note">${escapeHtml(SAFETY_NOTE_TEXT)}</p>
        ${errorHtml()}
        <div class="safety-actions">
          <button type="button" class="ghost safety-btn" data-role="safety-close">${escapeHtml(SAFETY_CANCEL_LABEL)}</button>
          ${omitReportee ? "" : `<button type="button" class="ghost safety-btn safety-dng" data-role="safety-block" ${selectedId ? "" : "disabled"}>${escapeHtml(SAFETY_BLOCK_LABEL)}</button>`}
          <button type="button" class="safety-btn safety-solid" data-role="safety-report" ${selectedId || omitReportee ? "" : "disabled"}>${escapeHtml(SAFETY_REPORT_LABEL)}</button>
        </div>
      </div>`;
    wireTargetStep();
  };

  const showError = (text) => {
    const errorEl = dialog.querySelector("[data-role=safety-error]");
    if (!errorEl) return;
    errorEl.textContent = text;
    errorEl.hidden = false;
  };

  const pickWho = () => {
    const checked = dialog.querySelector('input[name="safety-who"]:checked');
    if (!checked) return null;
    selectedId = checked.value;
    return selectedId;
  };

  const wireTargetStep = () => {
    for (const radio of dialog.querySelectorAll('input[name="safety-who"]')) {
      radio.addEventListener("change", () => {
        selectedId = radio.value;
        render(); // ボタンのdisabledと選択枠を反映
      });
    }
    dialog.querySelector("[data-role=safety-close]")?.addEventListener("click", close);
    dialog.querySelector("[data-role=safety-block]")?.addEventListener("click", submitBlock);
    dialog.querySelector("[data-role=safety-report]")?.addEventListener("click", () => {
      if (!pickWho() && !omitReportee) {
        showError(SAFETY_TARGET_ERROR_TEXT);
        return;
      }
      step = "reason";
      render();
    });
  };

  const wireReasonStep = () => {
    const submit = dialog.querySelector("[data-role=safety-report-submit]");
    for (const radio of dialog.querySelectorAll('input[name="safety-reason"]')) {
      radio.addEventListener("change", () => {
        submit.disabled = false;
      });
    }
    dialog.querySelector("[data-role=safety-back]").addEventListener("click", () => {
      step = "target";
      render();
    });
    submit.addEventListener("click", submitReport);
  };

  const reporteeName = () =>
    candidates.find((c) => c.id === selectedId)?.name ?? "";

  const submitBlock = async () => {
    const targetId = selectedId;
    if (!targetId) {
      showError(SAFETY_TARGET_ERROR_TEXT);
      return;
    }
    try {
      await client.call("POST", `/v1/users/${targetId}/block`);
      step = "done";
      doneMessage = `${reporteeName()}さんをブロックしました。今後この相手とLATCHが成立することはありません。`;
      render();
      await onBlocked?.(); // 詳細再取得(D-23の表示収束)
    } catch (err) {
      showError(err?.code === "RATE_LIMITED" ? RATE_LIMIT_TEXT : SAFETY_ERROR_TEXT);
    }
  };

  const submitReport = async () => {
    const reason = dialog.querySelector('input[name="safety-reason"]:checked')?.value;
    if (!reason) {
      showError(SAFETY_REASON_ERROR_TEXT);
      return;
    }
    const body = { latch_id: latchId, reason };
    if (fixedId) body.reportee_id = fixedId;
    else if (selectedId) body.reportee_id = selectedId;
    // omitReportee(提案1対1)はreportee_idなしのまま送る(案X)
    try {
      await client.call("POST", "/v1/reports", { body });
      const name = reporteeName();
      step = "done";
      doneMessage = `${
        name ? `${name}さんについての` : ""
      }通報を受け付けました。運営が内容を確認します。`;
      render();
    } catch (err) {
      showError(err?.code === "RATE_LIMITED" ? RATE_LIMIT_TEXT : SAFETY_ERROR_TEXT);
    }
  };

  const open = ({ latchId: id, participants = null, meId = null }) => {
    latchId = id;
    candidates = [];
    fixedId = null;
    selectedId = null;
    step = "target";
    doneMessage = "";
    omitReportee = participants == null;
    if (participants) {
      const others = participants.filter((p) => p.user_id !== meId);
      if (others.length === 1) {
        fixedId = others[0].user_id; // 1対1: 相手固定(選択済みで出す)
        selectedId = fixedId;
      }
      candidates = others.map((p) => ({ id: p.user_id, name: p.display_name }));
    }
    render();
    if (!dialog.open) dialog.showModal();
  };

  const close = () => {
    if (dialog.open) dialog.close();
  };

  // backdropクリックで閉じる(Escは<dialog>標準)
  dialog.addEventListener("click", (event) => {
    if (event.target === dialog) close();
  });

  return { open, close };
};
