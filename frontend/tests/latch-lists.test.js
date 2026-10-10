import { describe, expect, it, vi } from "vitest";
import {
  HIDDEN_PROPOSAL_TEXT,
  MATCHED_NOTICE_TITLE,
  NEXT_ACTION_TEXT,
  NO_LATCH_NOTE,
  NO_LATCH_TITLE,
  NO_MATCHED_NOTE,
  NO_MATCHED_TITLE,
  PROPOSAL_NOTICE_TITLE,
} from "../src/latch/texts.js";
import { latchRowHtml, createLatchList } from "../src/latch/lists.js";

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

describe("latchRowHtml(D-11・03のリスト部品の踏襲)", () => {
  it("候補(全形): 原文風の見出し(時刻+場所)+人数+残時間meta+acc pill(回答まち)+行リンク", () => {
    const html = latchRowHtml(latch("proposed"), NOW, { first: true });
    expect(html).toContain(`href="#/latches/${LATCH_ID}"`);
    expect(html).toContain("2026-10-01 20:00 天文館周辺");
    expect(html).toContain("3人");
    expect(html).toContain("あと1日と3時間で締切");
    expect(html).toContain('class="sb-pill acc"');
    expect(html).toContain("回答まち");
    expect(html).not.toContain("data-border"); // 先頭行は罫線なし
  });

  it("候補(最小形・hidden_until_match): 見出しは隠蔽文言・metaは残時間のみ", () => {
    const html = latchRowHtml(latch("partial_accept", {}, minimalProposal), NOW);
    expect(html).toContain(HIDDEN_PROPOSAL_TEXT);
    expect(html).not.toContain("3人");
    expect(html).toContain("あと1日と3時間で締切");
  });

  it("成立: 見出し+「チャットで挨拶を交わしましょう」meta+成立済みpill", () => {
    const html = latchRowHtml(latch("matched"), NOW, { first: false });
    expect(html).toContain(NEXT_ACTION_TEXT);
    expect(html).toContain("成立済み");
    expect(html).toContain("data-border"); // 2行目以降は罫線つき
  });

  it("完了: pillはgone様式(完了)", () => {
    const html = latchRowHtml(latch("completed"), NOW);
    expect(html).toContain('class="sb-pill gone"');
    expect(html).toContain("完了");
  });
});

const mountList = (callImpl, mode = "proposals") => {
  document.body.innerHTML = `
    <div class="page-main page-main--lists" id="${mode}Screen">
      <section class="list-hero">
        <p class="pv-badge" data-list-badge hidden><span class="pv-badge-dot"></span><span data-list-badge-text></span></p>
        <h1 class="list-title" data-list-title></h1>
      </section>
      <section class="sb-others-sec">
        <ul class="sb-others" data-list-rows></ul>
        <button class="ghost notice-more" data-list-more type="button" hidden>もっと見る</button>
      </section>
    </div>`;
  const root = document.getElementById(`${mode}Screen`);
  const client = { call: vi.fn(callImpl) };
  const onCounts = vi.fn();
  const list = createLatchList({ client, root, mode, onCounts });
  return { client, root, onCounts, list };
};

describe("createLatchList(D-11簡易一覧)", () => {
  it("proposals: 候補のみを描き・見出しは承認済み文言・バッジに件数", async () => {
    const { root, list, onCounts } = mountList(async () => ({
      items: [latch("proposed"), latch("matched")],
      next_cursor: null,
    }));
    await list.show();
    expect(root.querySelector("[data-list-title]").textContent).toBe(
      PROPOSAL_NOTICE_TITLE,
    );
    expect(root.querySelector("[data-list-badge-text]").textContent).toBe(
      "LATCH候補 · 1件",
    );
    expect(root.querySelectorAll(".sb-row").length).toBe(1); // matchedは出ない
    expect(root.querySelector("[data-list-badge]").hidden).toBe(false);
    expect(onCounts).toHaveBeenCalledWith({ proposals: 1 });
  });

  it("matched: 成立のみを描く", async () => {
    const { root, list, onCounts } = mountList(async () => ({
      items: [latch("proposed"), latch("matched"), latch("completed")],
      next_cursor: null,
    }), "matched");
    await list.show();
    expect(root.querySelector("[data-list-title]").textContent).toBe(
      MATCHED_NOTICE_TITLE,
    );
    expect(root.querySelector("[data-list-badge-text]").textContent).toBe(
      "成立 · 2件",
    );
    expect(root.querySelectorAll(".sb-row").length).toBe(2);
    expect(onCounts).toHaveBeenCalledWith({ matched: 2 });
  });

  it("0件は空状態(既存の一文+補足)", async () => {
    const { root, list } = mountList(async () => ({ items: [], next_cursor: null }));
    await list.show();
    expect(root.querySelector(".latch-empty").textContent).toContain(NO_LATCH_TITLE);
    expect(root.querySelector(".latch-empty").textContent).toContain(NO_LATCH_NOTE);
  });

  it("成立0件は成立側の空状態文言", async () => {
    const { root, list } = mountList(async () => ({ items: [], next_cursor: null }), "matched");
    await list.show();
    expect(root.querySelector(".latch-empty").textContent).toContain(NO_MATCHED_TITLE);
    expect(root.querySelector(".latch-empty").textContent).toContain(NO_MATCHED_NOTE);
  });

  it("next_cursorが残れば「もっと見る」・追頁でcursorを渡して累積する", async () => {
    const pages = [
      { items: [latch("proposed")], next_cursor: "c1" },
      { items: [latch("partial_accept")], next_cursor: null },
    ];
    const { client, root, list } = mountList(async () => pages.shift());
    await list.show();
    expect(root.querySelector("[data-list-more]").hidden).toBe(false);
    await list.loadMore();
    expect(client.call.mock.calls.at(-1)).toEqual([
      "GET",
      "/v1/latches?limit=20&cursor=c1",
    ]);
    expect(root.querySelectorAll(".sb-row").length).toBe(2); // 累積
    expect(root.querySelector("[data-list-badge-text]").textContent).toBe(
      "LATCH候補 · 2件",
    );
    expect(root.querySelector("[data-list-more]").hidden).toBe(true);
  });
});
