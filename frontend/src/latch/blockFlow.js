// ブロック登録flow(M3 ws-8 design §2.7・§5-1承認)。成立済み詳細の導線
// (通報と同型)。1対1=相手固定・グループ=対象者選択。POST /v1/users/{id}/block
// はパス指定(bodyなし・冪等201)。成功でトースト+onBlocked(詳細再取得 —
// D-23のcancelled化・チャット読取専用化は登録APIのtx内でサーバ側が自動適用
// 済みのため、再取得応答で自然に収束する)。
import {
  BLOCK_CANCEL_LABEL,
  BLOCK_CONFIRM_TEXT,
  BLOCK_ERROR_TEXT,
  BLOCK_SELECT_ERROR_TEXT,
  BLOCK_SELECT_LABEL,
  BLOCK_SUBMIT_LABEL,
  BLOCK_TOAST_TEXT,
  RATE_LIMIT_TEXT,
} from "./texts.js";
import { escapeHtml } from "./view.js";

export const createBlockFlow = ({ client, chrome, modal, onBlocked }) => {
  let fixedTargetId = null;
  let fixedName = "";
  let candidates = []; // [{id, name}] — グループ選択肢

  const showError = (text) => {
    const errorEl = modal.querySelector("[data-role=block-error]");
    errorEl.textContent = text;
    errorEl.hidden = false;
  };

  const render = () => {
    const targetHtml = fixedTargetId
      ? `<p class="block-target">ブロック対象: <strong>${escapeHtml(fixedName)}</strong></p>`
      : `<fieldset class="block-target-select"><legend>${BLOCK_SELECT_LABEL}</legend>${candidates
          .map(
            (c) =>
              `<label><input type="radio" name="blockee" value="${c.id}" /> ${escapeHtml(c.name)}</label>`,
          )
          .join("")}</fieldset>`;
    modal.innerHTML = `
      <section class="confirmation block-dialog" role="dialog" aria-modal="true" aria-labelledby="blockTitle">
        <button type="button" class="modal-close" data-role="block-close" aria-label="閉じる"><i class="ph ph-x"></i></button>
        <p class="section-kicker centered"><span></span>BLOCK</p>
        <h2 id="blockTitle">ブロック</h2>
        ${targetHtml}
        <p class="block-confirm">${BLOCK_CONFIRM_TEXT}</p>
        <p class="block-error" data-role="block-error" role="alert" hidden></p>
        <div class="block-actions">
          <button type="button" class="block-submit" data-role="block-submit">${BLOCK_SUBMIT_LABEL}</button>
          <button type="button" class="block-cancel" data-role="block-cancel">${BLOCK_CANCEL_LABEL}</button>
        </div>
      </section>
    `;
    modal.querySelector("[data-role=block-close]").addEventListener("click", close);
    modal.querySelector("[data-role=block-cancel]").addEventListener("click", close);
    modal.querySelector("[data-role=block-submit]").addEventListener("click", submit);
  };

  const open = ({ participants, meId }) => {
    fixedTargetId = null;
    fixedName = "";
    candidates = [];
    const others = (participants ?? []).filter((p) => p.user_id !== meId);
    if (others.length === 1) {
      fixedTargetId = others[0].user_id; // 1対1: 相手固定
      fixedName = others[0].display_name;
    } else {
      candidates = others.map((p) => ({ id: p.user_id, name: p.display_name }));
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
    const selected = modal.querySelector('input[name="blockee"]:checked')?.value;
    const targetId = fixedTargetId ?? selected;
    if (!targetId) {
      showError(BLOCK_SELECT_ERROR_TEXT);
      return;
    }
    try {
      await client.call("POST", `/v1/users/${targetId}/block`);
      close();
      chrome.showToast(BLOCK_TOAST_TEXT);
      await onBlocked?.(); // 詳細再取得(D-23の表示収束)
    } catch (err) {
      showError(err?.code === "RATE_LIMITED" ? RATE_LIMIT_TEXT : BLOCK_ERROR_TEXT);
    }
  };

  // backdropクリックで閉じる(reportModalと同型・一度だけwire)
  modal.addEventListener("click", (event) => {
    if (event.target === modal && !modal.hidden) close();
  });

  return { open, submit, close };
};
