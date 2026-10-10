import { describe, expect, it, vi } from "vitest";
import {
  BLOCKS_EMPTY_TEXT,
  BLOCK_CANCEL_LABEL,
  BLOCK_CONFIRM_SUFFIX,
  BLOCK_UNBLOCK_LABEL,
  RATE_LIMIT_TEXT,
} from "../src/latch/texts.js";
import { createBlocksSection } from "../src/latch/blocks.js";

const PEER = "33333333-3333-4333-8333-333333333333";
const OTHER = "44444444-4444-4444-8444-444444444444";
const block = (id, name) => ({
  blocked_id: id,
  display_name: name,
  created_at: "2026-10-01T09:00:00+09:00",
});
const apiError = (status, code) =>
  Object.assign(new Error("x"), { name: "ApiError", status, code });

const mountBlocks = (callImpl) => {
  const client = { call: vi.fn(callImpl ?? (async () => ({ items: [], next_cursor: null }))) };
  const el = {
    list: document.createElement("div"),
    moreButton: document.createElement("button"),
  };
  el.moreButton.hidden = true;
  // UI v2 Phase 5: 解除確認は旧modal-backdropから<dialog class="dlg">へ
  const dialog = document.createElement("dialog");
  const blocks = createBlocksSection({ client, el, dialog });
  return { client, el, dialog, blocks };
};

describe("ブロック管理(design §2.6・UI v2 Phase 5で<dialog>へ)", () => {
  it("loadはGET /v1/users/me/blocks?limit=20・行=表示名+ブロック日+解除ボタン(表示名はエスケープ)", async () => {
    const { client, el, blocks } = mountBlocks(async () => ({
      items: [block(PEER, '<script>alert(1)</script>')],
      next_cursor: null,
    }));
    await blocks.load();
    expect(client.call).toHaveBeenCalledWith("GET", "/v1/users/me/blocks?limit=20");
    expect(el.list.querySelectorAll(".block-row").length).toBe(1);
    expect(el.list.querySelector(".block-name").innerHTML)
      .not.toContain("<script>");
    expect(el.list.querySelector(".block-name").textContent)
      .toBe('<script>alert(1)</script>');
    expect(el.list.textContent).toContain("10月1日 09:00 にブロック");
    expect(el.list.querySelector("[data-role=unblock-entry]").textContent)
      .toBe(BLOCK_UNBLOCK_LABEL);
    expect(el.list.querySelector("[data-role=unblock-entry]").getAttribute("aria-haspopup"))
      .toBe("dialog"); // 解除はdialogを開く(prototype/v2の規約)
  });

  it("0件は空状態文言", async () => {
    const { el, blocks } = mountBlocks();
    await blocks.load();
    expect(el.list.textContent).toContain(BLOCKS_EMPTY_TEXT);
  });

  it("next_cursorが残れば「もっと見る」・追頁でcursorを渡す", async () => {
    const pages = [
      { items: [block(PEER, "相手")], next_cursor: "c1" },
      { items: [block(OTHER, "他者")], next_cursor: null },
    ];
    const { client, el, blocks } = mountBlocks(async () => pages.shift());
    await blocks.load();
    expect(el.moreButton.hidden).toBe(false);
    await blocks.loadMore();
    expect(client.call.mock.calls.at(-1)).toEqual([
      "GET",
      "/v1/users/me/blocks?limit=20&cursor=c1",
    ]);
    expect(el.list.querySelectorAll(".block-row").length).toBe(2);
    expect(el.moreButton.hidden).toBe(true);
  });

  it("解除は確認dialog(表示名入り文言)を挟み[解除する]でDELETE→204で行除去", async () => {
    const { client, el, dialog, blocks } = mountBlocks(async (method) => {
      if (method === "DELETE") return null; // 204
      return { items: [block(PEER, "相手")], next_cursor: null };
    });
    await blocks.load();
    el.list.querySelector("[data-role=unblock-entry]").click();
    expect(dialog.open).toBe(true);
    expect(dialog.querySelector("h2").textContent).toBe("ブロックの解除");
    expect(dialog.textContent).toContain(`相手${BLOCK_CONFIRM_SUFFIX}`);
    dialog.querySelector("[data-role=unblock-submit]").click();
    await Promise.resolve();
    await Promise.resolve();
    await Promise.resolve();
    await Promise.resolve();
    expect(client.call).toHaveBeenCalledWith("DELETE", `/v1/users/${PEER}/block`);
    expect(dialog.open).toBe(false);
    expect(el.list.querySelectorAll(".block-row").length).toBe(0); // 行除去
    expect(el.list.textContent).toContain(BLOCKS_EMPTY_TEXT);
  });

  it("確認dialogの[やめる]ではDELETEしない", async () => {
    const { client, el, dialog, blocks } = mountBlocks(async () => ({
      items: [block(PEER, "相手")],
      next_cursor: null,
    }));
    await blocks.load();
    el.list.querySelector("[data-role=unblock-entry]").click();
    dialog.querySelector("[data-role=unblock-cancel]").click();
    expect(client.call).toHaveBeenCalledTimes(1); // GET のみ
    expect(dialog.open).toBe(false);
  });

  it("404(既に解除済み)は通信エラー扱いにせず一覧を取り直して収束", async () => {
    let failDelete = false;
    let listCalls = 0;
    const { el, dialog, blocks } = mountBlocks(async (method) => {
      if (method === "DELETE") {
        if (failDelete) throw apiError(404, "NOT_FOUND");
        return null;
      }
      listCalls += 1;
      return listCalls === 1
        ? { items: [block(PEER, "相手")], next_cursor: null }
        : { items: [], next_cursor: null };
    });
    await blocks.load();
    failDelete = true;
    el.list.querySelector("[data-role=unblock-entry]").click();
    dialog.querySelector("[data-role=unblock-submit]").click();
    await Promise.resolve();
    await Promise.resolve();
    await Promise.resolve();
    await Promise.resolve();
    await Promise.resolve();
    expect(listCalls).toBe(2); // 一覧を取り直した(収束)
    expect(el.list.textContent).toContain(BLOCKS_EMPTY_TEXT);
  });

  it("429は既存RATE_LIMIT_TEXT・dialogは開いたまま", async () => {
    const { client, el, dialog, blocks } = mountBlocks(async (method) => {
      if (method === "DELETE") throw apiError(429, "RATE_LIMITED");
      return { items: [block(PEER, "相手")], next_cursor: null };
    });
    await blocks.load();
    el.list.querySelector("[data-role=unblock-entry]").click();
    dialog.querySelector("[data-role=unblock-submit]").click();
    await Promise.resolve();
    await Promise.resolve();
    await Promise.resolve();
    await Promise.resolve();
    expect(client.call).toHaveBeenCalledTimes(2); // GET + DELETE
    expect(dialog.open).toBe(true);
    expect(dialog.textContent).toContain(RATE_LIMIT_TEXT);
    expect(dialog.textContent).toContain(BLOCK_CANCEL_LABEL);
  });
});
