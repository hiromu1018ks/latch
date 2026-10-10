import { afterEach, describe, expect, it, vi } from "vitest";
import { act, cleanup, fireEvent, render, waitFor } from "@testing-library/react";
import { BlocksSection } from "../src/latch/BlocksSection.jsx";

// 旧createBlocksSection(素のDOM配線)の試験を、Reactコンポーネント
// (BlocksSection・UI v2 Phase 6)のRTLへ書き換えたもの。意図=一覧の行様式・
// 空状態・追頁・解除の確認dialog(<dialog class="dlg">)・404収束・429表示は踏襲。
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
  render(<BlocksSection client={client} />);
  return { client };
};

const dialog = () => document.getElementById("unblockDialog");
const rows = () => [...document.querySelectorAll(".block-row")];

afterEach(() => {
  cleanup();
});

describe("ブロック管理(design §2.6)", () => {
  it("loadはGET /v1/users/me/blocks?limit=20・行=表示名+ブロック日+解除ボタン(表示名はテキスト)", async () => {
    const { client } = mountBlocks(async () => ({
      items: [block(PEER, '<script>alert(1)</script>')],
      next_cursor: null,
    }));
    await waitFor(() => expect(rows().length).toBe(1));
    expect(client.call).toHaveBeenCalledWith("GET", "/v1/users/me/blocks?limit=20");
    const row = rows()[0];
    expect(row.querySelector(".block-name").textContent)
      .toBe('<script>alert(1)</script>'); // テキストノードとして組まれる(XSS対策)
    expect(row.querySelector(".block-name img")).toBeNull();
    expect(row.textContent).toContain("10月1日 09:00 にブロック");
    expect(row.querySelector("button.block-unblock").textContent).toBe("解除する");
    expect(row.querySelector("button.block-unblock").getAttribute("aria-haspopup")).toBe("dialog");
    expect(row.querySelector("button.block-unblock").getAttribute("aria-label")).toBe(
      "<script>alert(1)</script>さんのブロックを解除",
    );
  });

  it("0件は空状態文言", async () => {
    mountBlocks();
    await waitFor(() => {
      expect(document.querySelector(".latch-empty")).not.toBeNull();
    });
    expect(document.querySelector(".latch-empty").textContent).toContain(
      "ブロックしているユーザーはいません",
    );
  });

  it("next_cursorが残れば「もっと見る」・追頁でcursorを渡す", async () => {
    const pages = [
      { items: [block(PEER, "相手")], next_cursor: "c1" },
      { items: [block(OTHER, "他者")], next_cursor: null },
    ];
    const { client } = mountBlocks(async () => pages.shift());
    await waitFor(() => {
      expect(document.querySelector("button.notice-more").hidden).toBe(false);
    });
    fireEvent.click(document.querySelector("button.notice-more"));
    await waitFor(() => {
      expect(client.call.mock.calls.at(-1)).toEqual([
        "GET",
        "/v1/users/me/blocks?limit=20&cursor=c1",
      ]);
      expect(rows().length).toBe(2);
      expect(document.querySelector("button.notice-more").hidden).toBe(true);
    });
  });

  it("解除は確認dialog(表示名入り文言)を挟み[解除する]でDELETE→204で行除去", async () => {
    const { client } = mountBlocks(async (method) => {
      if (method === "DELETE") return null; // 204
      return { items: [block(PEER, "相手")], next_cursor: null };
    });
    await waitFor(() => expect(rows().length).toBe(1));
    fireEvent.click(rows()[0].querySelector("button.block-unblock"));
    await waitFor(() => expect(dialog().open).toBe(true));
    expect(dialog().querySelector(".dlg-title").textContent).toBe("ブロックの解除");
    expect(dialog().textContent).toContain(`相手${"さんのブロックを解除しますか?"}`);
    fireEvent.click(dialog().querySelector(".safety-solid"));
    await waitFor(() => {
      expect(client.call).toHaveBeenCalledWith("DELETE", `/v1/users/${PEER}/block`);
      expect(dialog().open).toBe(false);
      expect(rows().length).toBe(0); // 行除去
      expect(document.querySelector(".latch-empty").textContent).toContain(
        "ブロックしているユーザーはいません",
      );
    });
  });

  it("確認dialogの[やめる]ではDELETEしない", async () => {
    const { client } = mountBlocks(async () => ({
      items: [block(PEER, "相手")],
      next_cursor: null,
    }));
    await waitFor(() => expect(rows().length).toBe(1));
    fireEvent.click(rows()[0].querySelector("button.block-unblock"));
    await waitFor(() => expect(dialog().open).toBe(true));
    fireEvent.click(dialog().querySelector(".ghost.safety-btn"));
    await act(async () => {});
    expect(client.call).toHaveBeenCalledTimes(1); // GET のみ
    expect(dialog().open).toBe(false);
  });

  it("404(既に解除済み)は通信エラー扱いにせず一覧を取り直して収束", async () => {
    let failDelete = false;
    let listCalls = 0;
    mountBlocks(async (method) => {
      if (method === "DELETE") {
        if (failDelete) throw apiError(404, "NOT_FOUND");
        return null;
      }
      listCalls += 1;
      return listCalls === 1
        ? { items: [block(PEER, "相手")], next_cursor: null }
        : { items: [], next_cursor: null };
    });
    await waitFor(() => expect(rows().length).toBe(1));
    failDelete = true;
    fireEvent.click(rows()[0].querySelector("button.block-unblock"));
    await waitFor(() => expect(dialog().open).toBe(true));
    fireEvent.click(dialog().querySelector(".safety-solid"));
    await waitFor(() => {
      expect(listCalls).toBe(2); // 一覧を取り直した(収束)
      expect(document.querySelector(".latch-empty").textContent).toContain(
        "ブロックしているユーザーはいません",
      );
    });
  });

  it("429は既存RATE_LIMIT_TEXT・dialogは開いたまま", async () => {
    mountBlocks(async (method) => {
      if (method === "DELETE") throw apiError(429, "RATE_LIMITED");
      return { items: [block(PEER, "相手")], next_cursor: null };
    });
    await waitFor(() => expect(rows().length).toBe(1));
    fireEvent.click(rows()[0].querySelector("button.block-unblock"));
    await waitFor(() => expect(dialog().open).toBe(true));
    fireEvent.click(dialog().querySelector(".safety-solid"));
    await waitFor(() => {
      expect(dialog().textContent).toContain(
        "操作が集中しています。少し時間をおいてもう一度お試しください。",
      );
      expect(dialog().textContent).toContain("やめる");
      expect(dialog().open).toBe(true);
    });
  });
});
