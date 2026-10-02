// ブロック管理(M3 ws-8 design §2.6)。設定画面の2セクションの一つ。一覧+
// 解除(確認モーダル挟み — 再登録導線が成立済み詳細しかないため誤解除の回復
// コストが高い)。解除の204で行除去(カーソル保持)・404(既に解除済み)は通信
// エラー扱いにせず一覧を取り直して収束させる。遡及効果なしはUIで説明しない。
import {
  BLOCK_CANCEL_LABEL,
  BLOCK_CONFIRM_SUFFIX,
  BLOCK_ERROR_TEXT,
  BLOCK_UNBLOCK_LABEL,
  BLOCKS_EMPTY_TEXT,
  RATE_LIMIT_TEXT,
} from "./texts.js";
import { escapeHtml, noticeTimeText } from "./view.js";

export const createBlocksSection = ({ client, el, modal }) => {
  let items = [];
  let cursor = null;
  let pending = null; // 解除確認中の行

  const render = () => {
    el.list.innerHTML = items.length
      ? items
          .map(
            (item) => `<div class="block-row">
              <span class="block-name">${escapeHtml(item.display_name)}</span>
              <span class="block-date">${escapeHtml(noticeTimeText(item.created_at))}</span>
              <button type="button" class="block-unblock" data-role="unblock-entry" data-id="${item.blocked_id}">${BLOCK_UNBLOCK_LABEL}</button>
            </div>`,
          )
          .join("")
      : `<p class="latch-empty"><strong>${BLOCKS_EMPTY_TEXT}</strong></p>`;
    el.moreButton.hidden = cursor == null;
    wireRows();
  };

  const wireRows = () => {
    for (const button of el.list.querySelectorAll("[data-role=unblock-entry]")) {
      button.addEventListener("click", () => {
        pending = items.find((item) => item.blocked_id === button.dataset.id) ?? null;
        renderModal();
      });
    }
  };

  const showError = (text) => {
    const errorEl = modal.querySelector("[data-role=unblock-error]");
    errorEl.textContent = text;
    errorEl.hidden = false;
  };

  const renderModal = () => {
    if (!pending) return;
    modal.innerHTML = `
      <section class="confirmation block-dialog" role="dialog" aria-modal="true" aria-labelledby="unblockTitle">
        <button type="button" class="modal-close" data-role="unblock-close" aria-label="閉じる"><i class="ph ph-x"></i></button>
        <p class="section-kicker centered"><span></span>BLOCK</p>
        <h2 id="unblockTitle">ブロックの解除</h2>
        <p class="block-confirm">${escapeHtml(pending.display_name)}${BLOCK_CONFIRM_SUFFIX}</p>
        <p class="block-error" data-role="unblock-error" role="alert" hidden></p>
        <div class="block-actions">
          <button type="button" class="block-submit" data-role="unblock-submit">${BLOCK_UNBLOCK_LABEL}</button>
          <button type="button" class="block-cancel" data-role="unblock-cancel">${BLOCK_CANCEL_LABEL}</button>
        </div>
      </section>
    `;
    modal.hidden = false;
    document.body.classList.add("modal-open");
    modal.querySelector("[data-role=unblock-close]").addEventListener("click", close);
    modal.querySelector("[data-role=unblock-cancel]").addEventListener("click", close);
    modal.querySelector("[data-role=unblock-submit]").addEventListener("click", submit);
  };

  const close = () => {
    modal.hidden = true;
    document.body.classList.remove("modal-open");
    pending = null;
  };

  const submit = async () => {
    const target = pending;
    if (!target) return;
    try {
      await client.call("DELETE", `/v1/users/${target.blocked_id}/block`);
      items = items.filter((item) => item.blocked_id !== target.blocked_id);
      close();
      render(); // 行単位の除去(カーソル保持)
    } catch (err) {
      if (err?.status === 404) {
        close(); // 既に解除済み — 一覧を取り直して収束
        load().catch(() => {});
        return;
      }
      showError(err?.code === "RATE_LIMITED" ? RATE_LIMIT_TEXT : BLOCK_ERROR_TEXT);
    }
  };

  // backdropクリックで閉じる(reportModalと同型・一度だけwire)
  modal.addEventListener("click", (event) => {
    if (event.target === modal && !modal.hidden) close();
  });

  const load = async () => {
    const data = await client.call("GET", "/v1/users/me/blocks?limit=20");
    items = data.items;
    cursor = data.next_cursor;
    render();
  };

  const loadMore = async () => {
    if (cursor == null) return;
    const data = await client.call(
      "GET",
      `/v1/users/me/blocks?limit=20&cursor=${encodeURIComponent(cursor)}`,
    );
    items = items.concat(data.items);
    cursor = data.next_cursor;
    render();
  };

  return { load, loadMore };
};
