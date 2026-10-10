import { describe, expect, it, vi } from "vitest";
import { ApiError } from "../src/api/session.js";
import {
  CANCEL_DIALOG_TITLE,
  CLOSED_TEXT,
  LATCHED_TITLE,
  LEFT_LEAD_TEXT,
  LEFT_TITLE,
  LATCH_NOT_FOUND_TEXT,
  SAFETY_ENTRY_TEXT,
} from "../src/latch/texts.js";
import { createDetail } from "../src/latch/detail.js";

const LATCH_ID = "11111111-1111-4111-8111-111111111111";
const ME = "22222222-2222-4222-8222-222222222222";
const PEER = "33333333-3333-4333-8333-333333333333";
const DEADLINE = "2026-12-01T12:00:00+09:00"; // 遠い未来(残時間テストを安定化)

const fullProposal = {
  time_summary: "2026-10-10 20:30",
  area_name: "天文館周辺",
  headcount: 2,
  category_primary: "drinking",
  category_secondary: null,
  budget: { max: 5000 },
  match_level: "high",
};
const minimalProposal = { headcount: 2, match_level: "medium" };

const myIntent = {
  id: "aaaaaaaa-1111-4111-8111-111111111111",
  structured_intent: {
    category: { primary: "drinking", secondary: "軽く飲みたい" },
    time: { start: "2026-10-10T20:00:00+09:00", end: null },
    location: { name: "天文館", radius_m: null },
    budget: { max: 5000, currency: "JPY" },
    participants: { min: 2, max: 4 },
    participants_includes_owner: true,
  },
};

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
    me: { id: ME, display_name: "自見", profile: {} },
    ensureMe: vi.fn(async () => ({ id: ME, display_name: "自見", profile: {} })),
  };
  const chrome = { showToast: vi.fn(), openAppDialog: vi.fn() };
  const root = document.createElement("section");
  const safety = { open: vi.fn() };
  const detail = createDetail({ client, appState, chrome, root, safety });
  return { client, appState, chrome, root, safety, detail };
};

// GET詳細・GET intents一覧・GET messagesに応答する既定モック
const apiFor = (latch, intents = []) => async (method, path) => {
  if (path === `/v1/latches/${latch.id}/messages?limit=100`) {
    return { items: [], next_cursor: null };
  }
  if (path === "/v1/intents?status=active") return { items: intents, next_cursor: null };
  if (path.endsWith("/leave")) return { latch: { id: latch.id, status: "matched", left: true } };
  if (path.startsWith("/v1/latches/") && path.endsWith("/response")) {
    return { latch };
  }
  return { latch };
};

describe("04 提案姿(全面再構成・プロトタイプ04)", () => {
  it("全形: ヒーロー・4情報カード・照合表・残時間タイマー・回答2択(D-05)", async () => {
    const { root, detail } = makeDetail(apiFor(baseLatch(), [myIntent]));
    await detail.show(LATCH_ID);
    expect(root.querySelector("h1").textContent).toBe("条件が、揃いそうです。");
    // 4情報カード(開始=mono・エリア・人数・予算)
    expect(root.textContent).toContain("20:30");
    expect(root.textContent).toContain("今夜 · 10/10（土）");
    expect(root.textContent).toContain("天文館周辺");
    expect(root.textContent).toContain("¥5,000");
    expect(root.textContent).toContain("あなた＋1人");
    // 照合表(自分の条件→候補)
    expect(root.textContent).toContain("あなたの条件との照合");
    expect(root.textContent).toContain("5項目すべて条件内");
    expect(root.textContent).toContain("2〜4人（あなたを含む）");
    // 回答期限タイマー(role=timer)
    expect(root.querySelector('[role="timer"]')).not.toBeNull();
    // 回答2択(辞退するは廃止・D-05)
    const buttons = [...root.querySelectorAll("[data-role=respond]")];
    expect(buttons.map((b) => b.dataset.value)).toEqual(["yes", "defer"]);
    expect(buttons.map((b) => b.textContent)).toEqual(["参加する", "今回は見送る"]);
  });

  it("最小形(hidden_until_match): 4カード・照合表なし+案内ボックス+回答2択", async () => {
    const { root, detail } = makeDetail(
      apiFor(baseLatch({}, minimalProposal)),
    );
    await detail.show(LATCH_ID);
    expect(root.textContent).toContain("条件が合う候補があります");
    expect(root.textContent).toContain("全員が「参加する」と答えたときにわかります");
    expect(root.querySelector("[data-role=pv-fit]")).toBeNull(); // 照合表なし
    expect(root.querySelector(".pv-visual")).toBeNull(); // オーブなし
    expect(root.querySelectorAll("[data-role=respond]").length).toBe(2);
    expect(root.querySelector('[role="timer"]')).not.toBeNull();
  });

  it("照合表は自分のIntentを値マッチングで特定する(見つからない行は「—」)", async () => {
    const { root, detail } = makeDetail(apiFor(baseLatch(), [])); // active 0件
    await detail.show(LATCH_ID);
    expect(root.textContent).toContain("候補は、あなたの条件の範囲で選ばれています");
    expect(root.textContent).not.toContain("5項目すべて条件内");
  });

  it("回答期限タイマー: 分:秒・残5分以下でtimer-low", async () => {
    vi.useFakeTimers();
    try {
      const soon = new Date(Date.now() + 4 * 60 * 1000).toISOString();
      const { root, detail } = makeDetail(
        apiFor(baseLatch({ response_deadline: soon })),
      );
      await detail.show(LATCH_ID);
      const timer = root.querySelector('[role="timer"]');
      expect(timer.textContent).toMatch(/^\d+:\d{2}$/);
      expect(timer.classList.contains("timer-low")).toBe(true);
    } finally {
      vi.useRealTimers();
    }
  });

  it("open状態: 参加する→POST yes(pending制御つき)", async () => {
    const latch = baseLatch();
    const calls = [];
    const client = {
      call: vi.fn(async (method, path, opts) => {
        calls.push([method, path, opts]);
        if (path === `/v1/latches/${LATCH_ID}/response`) return { latch };
        return apiFor(latch, [myIntent])(method, path);
      }),
    };
    const { root, detail } = makeDetail((m, p, o) => client.call(m, p, o));
    await detail.show(LATCH_ID);
    const join = root.querySelector('[data-role=respond][data-value="yes"]');
    join.click();
    await vi.waitFor(() => {
      expect(calls.some(([m, p]) => m === "POST" && p.endsWith("/response"))).toBe(true);
    });
    expect(calls.find(([m, p]) => p.endsWith("/response"))[2]).toEqual({
      body: { response: "yes" },
    });
  });

  it("joined状態(my_response=yes): 待ち文言+「回答を取り消す」→POST undo", async () => {
    const calls = [];
    const latch = baseLatch({ status: "partial_accept", my_response: "yes", remaining_responses: 1 });
    const client = {
      call: vi.fn(async (method, path, opts) => {
        calls.push([method, path, opts]);
        if (path.endsWith("/response/undo")) return { latch: baseLatch() };
        return apiFor(latch, [myIntent])(method, path);
      }),
    };
    const { root, detail } = makeDetail((m, p, o) => client.call(m, p, o));
    await detail.show(LATCH_ID);
    expect(root.textContent).toContain("参加と回答しました。");
    expect(root.textContent).toContain("ほかの1人の回答を待っています");
    const undoButton = root.querySelector("[data-role=undo]");
    expect(undoButton.textContent).toBe("回答を取り消す");
    undoButton.click();
    await vi.waitFor(() => {
      expect(calls.some(([m, p]) => p.endsWith("/response/undo"))).toBe(true);
    });
  });

  it("グループ提案のjoined: 残回答から現況(誰が回答済みかは出さない)", async () => {
    const { root, detail } = makeDetail(
      apiFor(
        baseLatch(
          { is_group: true, status: "partial_accept", my_response: "yes", remaining_responses: 2 },
          { ...fullProposal, headcount: 3 },
        ),
        [myIntent],
      ),
    );
    await detail.show(LATCH_ID);
    expect(root.textContent).toContain("ほかの2人の回答を待っています。");
  });

  it("declined(未送信): chipsを選んで「待機中に戻る」でdefer+reasonsを送る(D-07)", async () => {
    const calls = [];
    const latch = baseLatch();
    const client = {
      call: vi.fn(async (method, path, opts) => {
        calls.push([method, path, opts]);
        if (path.endsWith("/response")) return { latch };
        return apiFor(latch, [myIntent])(method, path);
      }),
    };
    const { root, detail } = makeDetail((m, p, o) => client.call(m, p, o));
    await detail.show(LATCH_ID);
    // 見送る=即座には送らない(理由chips→離脱時に送信)
    root.querySelector('[data-role=respond][data-value="defer"]').click();
    expect(calls.some(([, p]) => p.endsWith("/response"))).toBe(false);
    expect(root.textContent).toContain("見送りました。");
    // 理由chips(任意・複数)。再描画でノードが変わるため都度取り直す
    const chips = [...root.querySelectorAll(".reason-chip")];
    expect(chips.map((c) => c.dataset.reason)).toEqual(["time", "place", "size", "mood"]);
    root.querySelector('.reason-chip[data-reason="time"]').click();
    root.querySelector('.reason-chip[data-reason="mood"]').click();
    await vi.waitFor(() => {
      expect(root.querySelector('.reason-chip[data-reason="time"]')?.getAttribute("aria-pressed")).toBe("true");
      expect(root.querySelector('.reason-chip[data-reason="mood"]')?.getAttribute("aria-pressed")).toBe("true");
    });
    // 待機中に戻る=defer+選択理由を送って03へ
    root.querySelector("[data-role=confirm-decline]").click();
    await vi.waitFor(() => {
      const posted = calls.find(([, p]) => p.endsWith("/response"));
      expect(posted).toBeTruthy();
    });
    expect(calls.find(([, p]) => p.endsWith("/response"))[2]).toEqual({
      body: { response: "defer", reasons: ["time", "mood"] },
    });
    await vi.waitFor(() => expect(window.location.hash).toBe("#/standby"));
  });

  it("declined(未送信)の取り消し: 画面操作のみでAPIを呼ばない", async () => {
    const calls = [];
    const latch = baseLatch();
    const { root, detail } = makeDetail(async (m, p, o) => {
      calls.push([m, p]);
      return apiFor(latch, [myIntent])(m, p);
    });
    await detail.show(LATCH_ID);
    root.querySelector('[data-role=respond][data-value="defer"]').click();
    root.querySelector("[data-role=cancel-decline]").click();
    expect(root.textContent).toContain("全員が「参加する」と答えたときだけ"); // openに戻る
    expect(calls.some(([, p]) => p.includes("/response"))).toBe(false);
  });

  it("declined(送信済み=my_response=defer・rejected): chips+「取り消す」→POST undo", async () => {
    const calls = [];
    const latch = baseLatch({ status: "rejected", my_response: "defer" });
    const client = {
      call: vi.fn(async (method, path, opts) => {
        calls.push([method, path, opts]);
        if (path.endsWith("/response/undo")) return { latch: baseLatch() };
        return apiFor(latch, [myIntent])(method, path);
      }),
    };
    const { root, detail } = makeDetail((m, p, o) => client.call(m, p, o));
    await detail.show(LATCH_ID);
    expect(root.textContent).toContain("見送りました。");
    expect(root.querySelectorAll(".reason-chip").length).toBe(4);
    root.querySelector("[data-role=undo]").click();
    await vi.waitFor(() => {
      expect(calls.some(([, p]) => p.endsWith("/response/undo"))).toBe(true);
    });
  });

  it("yes回答で全員がそろった場合: 04のallIn状態(チャットを開く→05)を一度だけ出す", async () => {
    const matched = baseLatch(
      { status: "matched", my_response: "yes", remaining_responses: 0 },
      minimalProposal,
    );
    matched.participants = participants;
    let state = baseLatch();
    const client = {
      call: vi.fn(async (method, path) => {
        if (path.endsWith("/response")) {
          state = matched; // 全員参加で成立
          return { latch: matched };
        }
        if (path === `/v1/latches/${LATCH_ID}/messages?limit=100`) {
          return { items: [], next_cursor: null };
        }
        return { latch: state };
      }),
    };
    const { root, detail } = makeDetail((m, p) => client.call(m, p));
    await detail.show(LATCH_ID);
    root.querySelector('[data-role=respond][data-value="yes"]').click();
    await vi.waitFor(() => {
      expect(root.textContent).toContain("全員が参加しました。");
    });
    expect(root.textContent).toContain("チャットを開く");
    // チャットを開く→05(成立ヒーロー+チャット)
    root.querySelector("[data-role=open-chat]").click();
    await vi.waitFor(() => {
      expect(root.querySelector(".lt-log")).not.toBeNull();
      expect(root.querySelector("h1").textContent).toBe(LATCHED_TITLE);
    });
  });

  it("flush: 未送信の見送りを画面離脱時に送る(理由つき)", async () => {
    const calls = [];
    const latch = baseLatch();
    const client = {
      call: vi.fn(async (method, path, opts) => {
        calls.push([method, path, opts]);
        if (path.endsWith("/response")) return { latch };
        return apiFor(latch, [myIntent])(method, path);
      }),
    };
    const { root, detail } = makeDetail((m, p, o) => client.call(m, p, o));
    await detail.show(LATCH_ID);
    root.querySelector('[data-role=respond][data-value="defer"]').click();
    root.querySelector('.reason-chip[data-reason="place"]').click();
    detail.flush();
    await vi.waitFor(() => {
      const posted = calls.find(([, p]) => p.endsWith("/response"));
      expect(posted).toBeTruthy();
    });
    expect(calls.find(([, p]) => p.endsWith("/response"))[2]).toEqual({
      body: { response: "defer", reasons: ["place"] },
    });
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

  it("undo不可(409)はトーストで伝える", async () => {
    const latch = baseLatch({ status: "rejected", my_response: "defer" });
    const chrome = { showToast: vi.fn(), openAppDialog: vi.fn() };
    const client = {
      call: vi.fn(async (method, path) => {
        if (path.endsWith("/response/undo")) {
          throw new ApiError(409, "LATCH_CLOSED", "closed");
        }
        return apiFor(latch, [myIntent])(method, path);
      }),
    };
    const root = document.createElement("section");
    const detail = createDetail({
      client,
      appState: { me: { id: ME, display_name: "自" }, ensureMe: async () => ({ id: ME, display_name: "自" }) },
      chrome,
      root,
      safety: { open: vi.fn() },
    });
    await detail.show(LATCH_ID);
    root.querySelector("[data-role=undo]").click();
    await vi.waitFor(() => {
      expect(chrome.showToast).toHaveBeenCalledWith("この回答は取り消せませんでした");
    });
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

  it("通報導線: 提案1対1=あり(safety統合dialog)/グループ提案=なし", async () => {
    // 提案1対1 → safety.open(participants: null)
    const oneOnOne = makeDetail(apiFor(baseLatch(), [myIntent]));
    await oneOnOne.detail.show(LATCH_ID);
    expect(oneOnOne.root.textContent).toContain("この提案を通報する");
    oneOnOne.root.querySelector("[data-role=report-entry]").click();
    expect(oneOnOne.safety.open).toHaveBeenCalledWith({
      latchId: LATCH_ID,
      participants: null,
      meId: ME,
    });
    // グループ提案(参加者非開示のため導線なし)
    const group = makeDetail(
      apiFor(baseLatch({ is_group: true, remaining_responses: 2 }), [myIntent]),
    );
    await group.detail.show(LATCH_ID);
    expect(group.root.textContent).not.toContain("通報");
  });
});

// --- UI v2 Phase 4: 05成立姿(全面再構成・プロトタイプ05) -----------------------
const matchedLatch = (overrides = {}, proposal = fullProposal) =>
  baseLatch(
    { status: "matched", my_response: "yes", remaining_responses: 0, participants, ...overrides },
    proposal,
  );

describe("05 成立姿(全面再構成・プロトタイプ05)", () => {
  const mountMatched = (latch = matchedLatch(), callImpl) => {
    const impl =
      callImpl ??
      ((method, path) => {
        if (path === `/v1/latches/${LATCH_ID}/messages?limit=100`) {
          return { items: [], next_cursor: null };
        }
        if (path === "/v1/intents?status=active") return { items: [myIntent], next_cursor: null };
        return { latch };
      });
    return makeDetail(impl);
  };

  it("ヒーロー: 「LATCHしました。」+成立kicker+条件タグ4種+snapアニメの弧", async () => {
    const { root, detail } = mountMatched();
    await detail.show(LATCH_ID);
    expect(root.querySelector("h1").textContent).toBe(LATCHED_TITLE);
    expect(root.querySelector(".lt-hero-kicker").textContent).toBe("成立");
    const tags = [...root.querySelectorAll(".lt-tag")].map((el) => el.textContent);
    expect(tags).toEqual(["20:30〜", "天文館周辺", "2人", "¥5,000前後"]);
    expect(root.querySelector(".snapL")).not.toBeNull();
    expect(root.querySelector(".snapR")).not.toBeNull();
    expect(root.querySelector(".lt-burst")).not.toBeNull();
  });

  it("aside: 成立した内容(details+dl)+カレンダーに追加+メンバー+安全のために+取り消し", async () => {
    const { root, detail } = mountMatched();
    await detail.show(LATCH_ID);
    // 成立した内容(dl 4行+カレンダーに追加=D-10)
    const details = root.querySelector(".lt-details");
    expect(details).not.toBeNull();
    expect(details.querySelector("summary").textContent).toContain("成立した内容");
    expect(details.textContent).toContain("今夜 20:30〜");
    expect(details.textContent).toContain("天文館周辺");
    expect(details.textContent).toContain("集合場所はチャットで");
    expect(details.textContent).toContain("1人 5,000円前後");
    expect(root.querySelector("[data-role=calendar-add]").textContent).toContain("カレンダーに追加");
    // メンバー(他者=色付き・自分=あなた+参加)
    const names = [...root.querySelectorAll(".lt-member-name")].map((el) => el.textContent);
    expect(names).toEqual(["相手", "あなた"]);
    expect(root.querySelectorAll(".lt-member-status").length).toBe(2);
    expect(root.textContent).toContain("お互いの表示名が見えるようになりました");
    // 安全のために+通報・ブロック(D-09統合dialogの入口)
    expect(root.textContent).toContain("会う前の安全ガイド");
    expect(root.textContent).toContain(SAFETY_ENTRY_TEXT);
    // この成立を取り消す(D-08)
    expect(root.querySelector("[data-role=cancel-entry]").textContent).toBe("この成立を取り消す");
  });

  it("メンバーの色付きイニシャル: 相手はパレット色・自分はme様式(枠つき)", async () => {
    const { root, detail } = mountMatched();
    await detail.show(LATCH_ID);
    const avatars = [...root.querySelectorAll(".lt-avatar--member")];
    expect(avatars[0].textContent).toBe("相");
    expect(avatars[0].getAttribute("style")).toContain("background:#F2B880");
    expect(avatars[1].className).toContain("me");
  });

  it("通報・ブロック(aside)はsafety統合dialogをparticipantsつきで開く(D-09)", async () => {
    const { root, safety, detail } = mountMatched();
    await detail.show(LATCH_ID);
    root.querySelector("[data-role=safety-entry]").click();
    expect(safety.open).toHaveBeenCalledWith({
      latchId: LATCH_ID,
      participants,
      meId: ME,
    });
  });

  it("カレンダーに追加のクリックで.icsダウンロード(anchor click)", async () => {
    const { root, detail } = mountMatched();
    await detail.show(LATCH_ID);
    const clickSpy = vi
      .spyOn(HTMLAnchorElement.prototype, "click")
      .mockImplementation(() => {});
    root.querySelector("[data-role=calendar-add]").click();
    expect(clickSpy).toHaveBeenCalledTimes(1);
    clickSpy.mockRestore();
  });

  it("成立の取り消し(D-08): dialogの影響3行を開き、取り消すでPOST leave→取り消し後姿", async () => {
    const calls = [];
    const { root, detail, chrome } = mountMatched(matchedLatch(), async (method, path, opts) => {
      calls.push([method, path, opts]);
      if (path === `/v1/latches/${LATCH_ID}/messages?limit=100`) {
        return { items: [], next_cursor: null };
      }
      if (path.endsWith("/leave")) return { latch: { id: LATCH_ID, status: "matched", left: true } };
      return { latch: matchedLatch() };
    });
    await detail.show(LATCH_ID);
    root.querySelector("[data-role=cancel-entry]").click();
    // openAppDialog(題+影響3行+元に戻せない)
    expect(chrome.openAppDialog).toHaveBeenCalledTimes(1);
    const dialogArg = chrome.openAppDialog.mock.calls[0][0];
    expect(dialogArg.title).toBe(CANCEL_DIALOG_TITLE);
    expect(dialogArg.body.textContent).toContain("あなたが参加を取り消しました");
    expect(dialogArg.body.textContent).toContain("あなたはこのチャットに書き込めなくなります");
    expect(dialogArg.body.textContent).toContain("残りの1人で会うかどうかは、1人が決めます");
    expect(dialogArg.body.textContent).toContain("この操作は元に戻せません。");
    // 取り消す=POST /v1/latches/{id}/leave(空body)
    const confirm = dialogArg.actions.find((a) => a.variant === "danger");
    expect(confirm.label).toBe("取り消す");
    confirm.onClick();
    await vi.waitFor(() => {
      const posted = calls.find(([m, p]) => m === "POST" && p.endsWith("/leave"));
      expect(posted).toBeTruthy();
    });
    expect(calls.find(([m, p]) => p.endsWith("/leave"))[2]).toEqual({ body: {} });
    // 取り消し後の姿(ヒーロー・メンバー・チャット入力・導線)
    await vi.waitFor(() => {
      expect(root.querySelector("h1").textContent).toBe(LEFT_TITLE);
    });
    expect(root.textContent).toContain(LEFT_LEAD_TEXT);
    expect(root.querySelector("[data-role=cancel-entry]")).toBeNull(); // 導線は消える
    const leftStatus = root.querySelector(".lt-member-status.left");
    expect(leftStatus.textContent).toBe("取り消し済み");
    const input = root.querySelector("input");
    expect(input.disabled).toBe(true);
    expect(input.placeholder).toBe("取り消したため、書き込めません");
  });

  it("leaveの409 ALREADY_LEFTでも取り消し後姿へ収束する", async () => {
    const { root, detail, chrome } = mountMatched(matchedLatch(), async (method, path) => {
      if (path === `/v1/latches/${LATCH_ID}/messages?limit=100`) {
        return { items: [], next_cursor: null };
      }
      if (path.endsWith("/leave")) {
        throw new ApiError(409, "ALREADY_LEFT", "already left");
      }
      return { latch: matchedLatch() };
    });
    await detail.show(LATCH_ID);
    root.querySelector("[data-role=cancel-entry]").click();
    const dialogArg = chrome.openAppDialog.mock.calls[0][0];
    const confirm = dialogArg.actions.find((a) => a.variant === "danger");
    confirm.onClick();
    await vi.waitFor(() => {
      expect(root.querySelector("h1").textContent).toBe(LEFT_TITLE);
    });
    expect(root.querySelector("[data-role=cancel-entry]")).toBeNull();
  });

  it("完了(completed)ではattendance(実際に会いましたか?)がasideに出る(D-14)", async () => {
    const { root, detail } = mountMatched(
      matchedLatch({ status: "completed" }),
    );
    await detail.show(LATCH_ID);
    expect(root.textContent).toContain("実際に会いましたか?");
    expect(root.querySelectorAll(".attendance-yes,.attendance-no").length).toBe(2);
  });

  it("matchedへ直行(再訪・再読み込み)は05のチャット画面(回答UIは出ない)", async () => {
    const { root, detail } = mountMatched();
    await detail.show(LATCH_ID);
    expect(root.querySelector(".lt-log")).not.toBeNull();
    expect(root.querySelectorAll("[data-role=respond]").length).toBe(0);
  });

  it("コメント送信(チャット): 成立直後の05でメッセージを送れる", async () => {
    let posted = null;
    const { root, detail } = mountMatched(matchedLatch(), async (method, path) => {
      if (path === `/v1/latches/${LATCH_ID}/messages?limit=100`) {
        return {
          items: posted ? [posted] : [],
          next_cursor: null,
        };
      }
      if (method === "POST" && path.endsWith("/messages")) {
        posted = { id: "m1", sender_id: ME, body: "よろしく", created_at: "2026-10-01T20:05:00+09:00" };
        return { message: posted };
      }
      return { latch: matchedLatch() };
    });
    await detail.show(LATCH_ID);
    const input = root.querySelector("input");
    input.value = "よろしく";
    input.dispatchEvent(new Event("input"));
    root
      .querySelector("form.lt-chat-form")
      .dispatchEvent(new Event("submit", { cancelable: true })); // 単行input+円形送信
    await vi.waitFor(() => {
      expect(root.querySelectorAll(".lt-bubble").length).toBe(1);
    });
    expect(root.querySelector(".lt-bubble").textContent).toBe("よろしく");
    expect(root.querySelectorAll(".lt-msg.mine").length).toBe(1); // 自分=右
  });
});
