import { afterEach, describe, expect, it, vi } from "vitest";
import { cleanup, fireEvent, render, waitFor } from "@testing-library/react";
import { LatchRow, ListsScreen } from "../src/latch/ListsScreen.jsx";
import { ChromeProvider } from "../src/ui/chrome.jsx";

// 旧lists.js(latchRowHtml+createLatchList)の試験を、Reactコンポーネント
// (ListsScreen・UI v2 Phase 6)のRTLへ書き換えたもの。意図=行の様式・見出し・
// バッジ・空状態・追頁は踏襲する。
const LATCH_ID = "11111111-1111-4111-8111-111111111111";
const NOW = "2026-10-01T09:00:00+09:00"; // 残時間27時間→「あと1日と3時間で締切」

const fullProposal = {
  time_summary: "2026-10-01 20:00",
  area_name: "天文館周辺",
  headcount: 3,
  category_primary: "drinking",
  category_secondary: null,
  budget: { max: 5000 },
  match_level: "high",
};
const minimalProposal = { headcount: 2, match_level: "low" }; // hidden_until_match形

const latch = (status, overrides = {}, proposal = fullProposal) => ({
  id: LATCH_ID,
  status,
  response_deadline: "2026-10-02T12:00:00+09:00",
  expires_at: "2026-10-05T12:00:00+09:00",
  created_at: "2026-10-01T09:00:00+09:00",
  completed_at: null,
  proposal,
  is_group: false,
  my_response: null,
  remaining_responses: 1,
  ...overrides,
});

describe("LatchRow(D-11・03のリスト部品の踏襲)", () => {
  const mountRow = (l, first) => {
    cleanup();
    render(<LatchRow latch={l} nowIso={NOW} first={first} />);
    return document.querySelector(".sb-row");
  };

  it("候補(全形): 原文風の見出し(時刻+場所)+人数+残時間meta+acc pill(回答まち)+行リンク", () => {
    const row = mountRow(latch("proposed"), true);
    const link = row.querySelector("a.sb-row-link");
    expect(link.getAttribute("href")).toBe(`#/latches/${LATCH_ID}`);
    expect(link.textContent).toContain("2026-10-01 20:00 天文館周辺");
    expect(link.textContent).toContain("3人");
    expect(link.textContent).toContain("あと1日と3時間で締切");
    expect(row.querySelector(".sb-pill").className).toContain("acc");
    expect(row.querySelector(".sb-pill").textContent).toBe("回答まち");
    expect(row.getAttribute("data-border")).toBeNull(); // 先頭行は罫線なし
  });

  it("候補(最小形・hidden_until_match): 見出しは隠蔽文言・metaは残時間のみ", () => {
    const row = mountRow(latch("partial_accept", {}, minimalProposal), false);
    expect(row.textContent).toContain("条件が合う候補があります");
    expect(row.textContent).not.toContain("3人");
    expect(row.textContent).toContain("あと1日と3時間で締切");
  });

  it("成立: 見出し+「チャットで挨拶を交わしましょう」meta+成立済みpill", () => {
    const row = mountRow(latch("matched"), false);
    expect(row.textContent).toContain("チャットで挨拶を交わしましょう");
    expect(row.textContent).toContain("成立済み");
    expect(row.getAttribute("data-border")).toBe("true"); // 2行目以降は罫線つき
  });

  it("完了: pillはgone様式(完了)", () => {
    const row = mountRow(latch("completed"), true);
    expect(row.querySelector(".sb-pill").className).toContain("gone");
    expect(row.querySelector(".sb-pill").textContent).toBe("完了");
  });
});

describe("ListsScreen(D-11簡易一覧)", () => {
  const mountList = (callImpl, mode = "proposals") => {
    cleanup();
    const client = { call: vi.fn(callImpl) };
    render(
      <ChromeProvider>
        <ListsScreen client={client} mode={mode} />
      </ChromeProvider>,
    );
    return { client };
  };

  it("proposals: 候補のみを描き・見出しは承認済み文言・バッジに件数", async () => {
    mountList(async () => ({
      items: [latch("proposed"), latch("matched")],
      next_cursor: null,
    }), "proposals");
    await waitFor(() => {
      expect(document.querySelector("[data-list-title]").textContent).toBe(
        "LATCH候補があります。",
      );
    });
    expect(document.querySelector("[data-list-badge-text]").textContent).toBe(
      "LATCH候補 · 1件",
    );
    expect(document.querySelectorAll(".sb-row").length).toBe(1); // matchedは出ない
    expect(document.querySelector("[data-list-badge]").hidden).toBe(false);
  });

  it("matched: 成立のみを描く", async () => {
    mountList(async () => ({
      items: [latch("proposed"), latch("matched"), latch("completed")],
      next_cursor: null,
    }), "matched");
    await waitFor(() => {
      expect(document.querySelector("[data-list-title]").textContent).toBe(
        "LATCHが成立しました。",
      );
    });
    expect(document.querySelector("[data-list-badge-text]").textContent).toBe(
      "成立 · 2件",
    );
    expect(document.querySelectorAll(".sb-row").length).toBe(2);
  });

  it("0件は空状態(既存の一文+補足)", async () => {
    mountList(async () => ({ items: [], next_cursor: null }), "proposals");
    await waitFor(() => {
      expect(document.querySelector(".latch-empty")).not.toBeNull();
    });
    const empty = document.querySelector(".latch-empty");
    expect(empty.textContent).toContain("まだ提案はありません");
    expect(empty.textContent).toContain("条件が重なると、ここに届きます。");
  });

  it("成立0件は成立側の空状態文言", async () => {
    mountList(async () => ({ items: [], next_cursor: null }), "matched");
    await waitFor(() => {
      expect(document.querySelector(".latch-empty")).not.toBeNull();
    });
    const empty = document.querySelector(".latch-empty");
    expect(empty.textContent).toContain("成立したLATCHはまだありません");
    expect(empty.textContent).toContain("成立すると、ここに並びます。");
  });

  it("next_cursorが残れば「もっと見る」・追頁でcursorを渡して累積する", async () => {
    const pages = [
      { items: [latch("proposed")], next_cursor: "c1" },
      { items: [latch("partial_accept")], next_cursor: null },
    ];
    const { client } = mountList(async () => pages.shift(), "proposals");
    await waitFor(() => {
      expect(document.querySelector("[data-list-more]").hidden).toBe(false);
    });
    fireEvent.click(document.querySelector("[data-list-more]"));
    await waitFor(() => {
      expect(client.call.mock.calls.at(-1)).toEqual([
        "GET",
        "/v1/latches?limit=20&cursor=c1",
      ]);
      expect(document.querySelectorAll(".sb-row").length).toBe(2); // 累積
      expect(document.querySelector("[data-list-badge-text]").textContent).toBe(
        "LATCH候補 · 2件",
      );
      expect(document.querySelector("[data-list-more]").hidden).toBe(true);
    });
  });
});
