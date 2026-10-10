// ブロック管理(M3 ws-8 design §2.6・UI v2 Phase 5で解除を<dialog class="dlg">へ)。
// 設定画面の2セクションの一つ。一覧+解除(確認dialog挟み — 再登録導線が
// 成立済み詳細しかないため誤解除の回復コストが高い)。解除の204で行除去
// (カーソル保持)・404(既に解除済み)は通信エラー扱いにせず一覧を取り直して
// 収束させる。遡及効果なしはUIで説明しない。行とdialogはv2部品
// (sb-row系の行・dlg+safety系の操作ボタン)で構成する。
import {
  BLOCK_CANCEL_LABEL,
  BLOCK_CONFIRM_SUFFIX,
  BLOCK_ERROR_TEXT,
  BLOCK_UNBLOCK_LABEL,
  BLOCKS_EMPTY_TEXT,
  RATE_LIMIT_TEXT,
} from "./texts.js";
import { escapeHtml, noticeTimeText } from "./view.js";

export const createBlocksSection = ({ client, el, dialog }) => {
  let items = [];
  let cursor = null;
  let pending = null; // 解除確認中の行

  const render = () => {
    el.list.innerHTML = items.length
      ? `<ul class="block-rows">${items
          .map(
            (item) => `<li class="block-row">
              <div class="sb-row-body">
                <span class="block-name">${escapeHtml(item.display_name)}</span>
                <span class="sb-row-meta">${escapeHtml(noticeTimeText(item.created_at))} にブロック</span>
              </div>
              <button type="button" class="ghost danger block-unblock" data-role="unblock-entry" data-id="${item.blocked_id}" aria-haspopup="dialog" aria-label="${escapeHtml(item.display_name)}さんのブロックを解除">${BLOCK_UNBLOCK_LABEL}</button>
            </li>`,
          )
          .join("")}</ul>`
      : `<p class="latch-empty"><strong>${BLOCKS_EMPTY_TEXT}</strong></p>`;
    el.moreButton.hidden = cursor == null;
    wireRows();
  };

  const wireRows = () => {
    for (const button of el.list.querySelectorAll("[data-role=unblock-entry]")) {
      button.addEventListener("click", () => {
        pending = items.find((item) => item.blocked_id === button.dataset.id) ?? null;
        renderDialog();
      });
    }
  };

  const showError = (text) => {
    const errorEl = dialog.querySelector("[data-role=unblock-error]");
    errorEl.textContent = text;
    errorEl.hidden = false;
  };

  // 確認dialog(<dialog class="dlg">・safety系の操作ボタンを踏襲)
  const renderDialog = () => {
    if (!pending) return;
    dialog.innerHTML = `
      <div class="dlg-col">
        <h2 class="dlg-title">ブロックの解除</h2>
        <p class="dlg-desc"><strong>${escapeHtml(pending.display_name)}</strong>${escapeHtml(BLOCK_CONFIRM_SUFFIX)}</p>
        <p class="safety-error" data-role="unblock-error" role="alert" hidden></p>
        <div class="safety-actions">
          <button type="button" class="ghost safety-btn" data-role="unblock-cancel">${BLOCK_CANCEL_LABEL}</button>
          <button type="button" class="safety-btn safety-solid" data-role="unblock-submit">${BLOCK_UNBLOCK_LABEL}</button>
        </div>
      </div>
    `;
    if (!dialog.open) dialog.showModal();
    dialog.querySelector("[data-role=unblock-cancel]").addEventListener("click", close);
    dialog.querySelector("[data-role=unblock-submit]").addEventListener("click", submit);
  };

  const close = () => {
    if (dialog.open) dialog.close();
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

  // backdropクリックで閉じる(Escは<dialog>標準)
  dialog.addEventListener("click", (event) => {
    if (event.target === dialog && dialog.open) close();
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
