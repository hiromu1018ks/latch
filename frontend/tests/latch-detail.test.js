import { describe, expect, it, vi } from "vitest";
import { ApiError } from "../src/api/session.js";
import { CLOSED_TEXT, LATCH_NOT_FOUND_TEXT } from "../src/latch/texts.js";
import { createDetail } from "../src/latch/detail.js";

const LATCH_ID = "11111111-1111-4111-8111-111111111111";
const ME = "22222222-2222-4222-8222-222222222222";
const PEER = "33333333-3333-4333-8333-333333333333";
const DEADLINE = "2026-12-01T12:00:00+09:00"; // 遠い未来(残時間テストを安定化)

const fullProposal = {
  time_summary: "2026-10-01 20:00",
  area_name: "天文館周辺",
  headcount: 2,
  category_primary: "drinking",
  category_secondary: null,
  budget: { max: 5000 },
  match_level: "high",
};
const minimalProposal = { headcount: 2, match_level: "medium" };

const baseLatch = (overrides = {}, proposal = fullProposal) => ({
  id: LATCH_ID,
  status: "proposed",
  response_deadline: DEADLINE,
  expires_at: "2026-12-04T12:00:00+09:00",
  created_at: "2026-10-01T09:00:00+09:00",
  completed_at: null,
  proposal,
  is_group: false,
  my_response: null,
  remaining_responses: 1,
  ...overrides,
});

const participants = [
  { user_id: ME, display_name: "自分", profile: { bio: "自分紹介" } },
  { user_id: PEER, display_name: "相手", profile: { bio: "よろしく" } },
];

const makeDetail = (callImpl) => {
  const client = { call: vi.fn(callImpl) };
  const appState = {
    me: { id: ME, display_name: "自分", profile: {} },
    ensureMe: vi.fn(async () => ({ id: ME, display_name: "自分", profile: {} })),
  };
  const chrome = { showToast: vi.fn() };
  const root = document.createElement("section");
  const reportFlow = { open: vi.fn() };
  const detail = createDetail({ client, appState, chrome, root, reportFlow });
  return { client, appState, chrome, root, reportFlow, detail };
};

// GET詳細とGET messagesの両方に応答する既定モック
const apiFor = (latch) => async (method, path) => {
  if (path === `/v1/latches/${latch.id}/messages?limit=100`) {
    return { items: [], next_cursor: null };
  }
  return { latch };
};

describe("detail 3姿(design §2.3)", () => {
  it("提案姿(全形): 条件サマリ・一致度・残時間・回答3択", async () => {
    const { root, detail } = makeDetail(apiFor(baseLatch()));
    await detail.show(LATCH_ID);
    expect(root.textContent).toContain("2026-10-01 20:00");
    expect(root.textContent).toContain("天文館周辺");
    expect(root.textContent).toContain("ひとり5,000円まで");
    expect(root.textContent).toContain("一致度 高");
    expect(root.textContent).toContain("で締切"); // 残時間表示
    const buttons = [...root.querySelectorAll(".respond-button")];
    expect(buttons.map((b) => b.dataset.value)).toEqual(["yes", "defer", "no"]);
    expect(buttons.map((b) => b.textContent))
      .toEqual(["参加する", "今回は見送る", "辞退する"]);
  });

  it("提案姿(最小形): 条件サマリなし・「条件が合う候補があります」+一致度・残時間", async () => {
    const { root, detail } = makeDetail(
      apiFor(baseLatch({}, minimalProposal)),
    );
    await detail.show(LATCH_ID);
    expect(root.textContent).toContain("条件が合う候補があります");
    expect(root.textContent).not.toContain("天文館周辺");
    expect(root.textContent).toContain("一致度 中");
    expect(root.textContent).toContain("で締切");
    expect(root.querySelectorAll(".respond-button").length).toBe(3);
  });

  it("回答済み(my_response=yes): 3択を差し替え「参加します」", async () => {
    const { root, detail } = makeDetail(
      apiFor(baseLatch({ status: "partial_accept", my_response: "yes", remaining_responses: 1 })),
    );
    await detail.show(LATCH_ID);
    expect(root.querySelectorAll(".respond-button").length).toBe(0);
    expect(root.textContent).toContain("参加します");
    expect(root.textContent).toContain("あと1人の回答が必要"); // partial_acceptの現況
  });

  it("グループ提案: 必要人数の現況を表示(誰が回答済みかは出さない)", async () => {
    const { root, detail } = makeDetail(
      apiFor(baseLatch({ is_group: true, remaining_responses: 2 })),
    );
    await detail.show(LATCH_ID);
    expect(root.textContent).toContain("あと2人の回答が必要");
  });

  it("成立済み姿: 参加者(表示名)・集合情報・次アクション・チャット領域", async () => {
    const matched = baseLatch(
      { status: "matched", remaining_responses: 0 },
      minimalProposal,
    );
    matched.participants = participants;
    matched.time_summary = "2026-10-01 20:00";
    matched.area_name = "天文館周辺";
    const { root, detail } = makeDetail(apiFor(matched));
    await detail.show(LATCH_ID);
    expect(root.textContent).toContain("相手"); // 参加者表示名
    expect(root.textContent).toContain("よろしく"); // プロフィールbio
    expect(root.textContent).toContain("2026-10-01 20:00");
    expect(root.textContent).toContain("チャットで挨拶を交わしましょう");
    expect(root.querySelector(".chat-messages")).not.toBeNull();
    expect(root.querySelectorAll(".respond-button").length).toBe(0); // 回答操作なし
  });

  it("completed姿: チャットは読取専用文言・attendance質問を表示", async () => {
    const completed = baseLatch(
      { status: "completed", remaining_responses: 0 },
      minimalProposal,
    );
    completed.participants = participants;
    completed.time_summary = "2026-10-01 20:00";
    completed.area_name = null;
    const { root, detail } = makeDetail(apiFor(completed));
    await detail.show(LATCH_ID);
    expect(root.textContent)
      .toContain("対象時刻を過ぎたため、このチャットは閲覧のみできます");
    expect(root.textContent).toContain("実際に会いましたか?");
    expect(root.querySelectorAll(".attendance-field button").length).toBe(2);
  });

  it("不成立(my_response=no): 自身の操作履歴「辞退しました」", async () => {
    const { root, detail } = makeDetail(
      apiFor(baseLatch({ status: "rejected", my_response: "no" })),
    );
    await detail.show(LATCH_ID);
    expect(root.textContent).toContain("辞退しました");
    expect(root.textContent).not.toContain(CLOSED_TEXT);
  });

  it("不成立(それ以外): 統一文言のみ(相手の回答種別は開示しない)", async () => {
    const { root, detail } = makeDetail(
      apiFor(baseLatch({ status: "cancelled", my_response: null })),
    );
    await detail.show(LATCH_ID);
    expect(root.textContent).toContain(CLOSED_TEXT);
  });

  it("404は「提案が見つかりません」+ホームへ戻る導線", async () => {
    const { root, detail } = makeDetail(() => {
      throw new ApiError(404, "NOT_FOUND", "not found");
    });
    await detail.show(LATCH_ID);
    expect(root.textContent).toContain(LATCH_NOT_FOUND_TEXT);
    expect(root.querySelector('a[href="#/"]')).not.toBeNull();
  });

  it("403も404と同じ扱い(参加者でない事実を開示しない)", async () => {
    const { root, detail } = makeDetail(() => {
      throw new ApiError(403, "FORBIDDEN", "forbidden");
    });
    await detail.show(LATCH_ID);
    expect(root.textContent).toContain(LATCH_NOT_FOUND_TEXT);
  });

  it("通報導線: 提案1対1=あり/グループ提案=なし/成立済み=あり", async () => {
    // 提案1対1
    const oneOnOne = makeDetail(apiFor(baseLatch()));
    await oneOnOne.detail.show(LATCH_ID);
    expect(oneOnOne.root.textContent).toContain("この提案を通報する");
    // グループ提案(参加者非開示のため導線なし)
    const group = makeDetail(
      apiFor(baseLatch({ is_group: true, remaining_responses: 2 })),
    );
    await group.detail.show(LATCH_ID);
    expect(group.root.textContent).not.toContain("通報");
    // 成立済み
    const matched = baseLatch({ status: "matched" }, minimalProposal);
    matched.participants = participants;
    matched.time_summary = "2026-10-01 20:00";
    matched.area_name = null;
    const done = makeDetail(apiFor(matched));
    await done.detail.show(LATCH_ID);
    expect(done.root.textContent).toContain("このLATCHを通報する");
  });
});
