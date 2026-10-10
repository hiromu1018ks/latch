import { afterEach, describe, expect, it, vi } from "vitest";
import {
  createStandby,
  mainCardHtml,
  othersRowHtml,
  orbHtml,
  withdrawnCardHtml,
} from "../src/latch/standby.js";

const INTENT_ID = "aaaaaaaa-1111-4111-8111-111111111111";
const LATCH_ID = "11111111-1111-4111-8111-111111111111";
const NOW = "2026-10-10T12:00:00+09:00";

const intent = (overrides = {}) => ({
  id: INTENT_ID,
  status: "active",
  version: 1,
  raw_text: "今日20時以降、天文館で2〜4人、予算5,000円までなら軽く飲みたい",
  structured_data: {},
  structured_intent: {
    category: { primary: "drinking", secondary: null },
    time: { start: "2026-10-10T20:00:00+09:00", end: null },
    location: { name: "天文館", radius_m: null },
    budget: { max: 5000, currency: "JPY" },
    participants: { min: 2, max: 4 },
  },
  expires_at: "2026-10-10T23:59:00+09:00",
  created_at: "2026-10-10T11:48:00+09:00",
  updated_at: "2026-10-10T11:48:00+09:00",
  ...overrides,
});

const latch = (status, overrides = {}) => ({
  id: LATCH_ID,
  status,
  response_deadline: "2026-12-01T12:00:00+09:00",
  expires_at: "2026-12-04T12:00:00+09:00",
  created_at: "2026-10-01T09:00:00+09:00",
  completed_at: null,
  proposal: { headcount: 2, match_level: "high" },
  is_group: false,
  my_response: null,
  remaining_responses: 1,
  ...overrides,
});

const mountStandby = (callImpl) => {
  document.body.innerHTML = `
    <div class="page-main page-main--standby" id="standbyScreen">
      <p class="sb-badge"><span class="beacon"></span><span data-sb-badge>待機中 · 0件</span></p>
      <h1 class="sb-title"><span data-sb-title-a></span><br><span data-sb-title-b></span></h1>
      <p class="sb-lead" data-sb-lead></p>
      <article class="sb-main" data-sb-main hidden></article>
      <div class="sb-orb" data-sb-orb hidden></div>
      <ul class="sb-others" data-sb-others></ul>
    </div>`;
  const root = document.getElementById("standbyScreen");
  const client = { call: vi.fn(callImpl) };
  const chrome = {
    showToast: vi.fn(),
    openAppDialog: vi.fn(),
  };
  const onCounts = vi.fn();
  const standby = createStandby({ client, chrome, root, onCounts });
  return { client, chrome, onCounts, root, standby };
};

afterEach(() => {
  window.location.hash = "";
});

describe("mainCardHtml(メイン条件カード・プロトタイプ03)", () => {
  it("pill・原文引用・条件タグ・残時間タイマー・条件を直す・取り下げるを組む", () => {
    const html = mainCardHtml(intent(), NOW);
    expect(html).toContain("飲み · 12分前に預けた");
    expect(html).toContain("待機中");
    expect(html).toContain(
      "「今日20時以降、天文館で2〜4人、予算5,000円までなら軽く飲みたい」",
    );
    expect(html).toContain(">20:00〜<");
    expect(html).toContain(">〜¥5,000<");
    expect(html).toContain('role="timer"'); // 1秒更新はcreateStandby側
    expect(html).toContain("23:59 の取り下げまで");
    expect(html).toContain(`href="#/confirm?intent=${INTENT_ID}"`); // 条件を直す=02編集
    expect(html).toContain("条件を直す");
    expect(html).toContain("取り下げる");
    expect(html).toContain('aria-haspopup="dialog"');
  });

  it("期限なしはタイマーを出さず「期限なし」", () => {
    const html = mainCardHtml(intent({ expires_at: null }), NOW);
    expect(html).toContain("期限なし");
    expect(html).not.toContain('role="timer"');
  });

  it("raw_textはエスケープされる", () => {
    const html = mainCardHtml(intent({ raw_text: '<img src=x onerror=alert(1)>' }), NOW);
    expect(html).not.toContain("<img");
    expect(html).toContain("&lt;img");
  });
});

describe("withdrawnCardHtml(取り下げ済み状態・プロトタイプ03のmainGone)", () => {
  it("取り下げ済みpill・案内文・もう一度預ける", () => {
    const html = withdrawnCardHtml(intent(), NOW);
    expect(html).toContain("取り下げ済み");
    expect(html).toContain("この条件は取り下げました。もう誰とも照らし合わされません。");
    expect(html).toContain('href="#/"');
    expect(html).toContain("もう一度預ける");
  });
});

describe("othersRowHtml(ほかに待機中の意思)", () => {
  it("原文・meta(種別 · 相対時間 · 期限)・直す・取り下げる", () => {
    const html = othersRowHtml(intent({ raw_text: "年収1,000万円以上なら転職を考える" }), NOW, {
      first: false,
    });
    expect(html).toContain("「年収1,000万円以上なら転職を考える」");
    expect(html).toContain("飲み · 12分前に預けた · 今日 23:59 まで有効");
    expect(html).toContain(`href="#/confirm?intent=${INTENT_ID}"`);
    expect(html).toContain('aria-label="年収1,000万円以上なら転職を考えるを直す"');
    expect(html).toContain('aria-label="年収1,000万円以上なら転職を考えるを取り下げる"');
    expect(html).toContain("data-border");
  });
});

describe("orbHtml(オーブ・条件チップ浮遊)", () => {
  it("2アーク+rippleと条件チップを組む", () => {
    const html = orbHtml(intent());
    expect(html).toContain("arcL");
    expect(html).toContain("arcR");
    expect(html).toContain('class="ripple d3"');
    expect(html).toContain(">20:00〜<");
    expect(html).toContain(">天文館<");
    expect(html).toContain(">2〜4人<");
    expect(html).toContain(">〜¥5,000<");
  });
});

describe("createStandby(03の配線)", () => {
  it("showは一覧を描き、先頭がメイン・残りがほかの意思・空状態", async () => {
    const others = [
      intent({ id: "bbbbbbbb-2222-4222-8222-222222222222", raw_text: "年収1,000万円以上なら転職を考える" }),
    ];
    const { root, standby, onCounts } = mountStandby(async (method, path) => {
      if (path === "/v1/intents?status=active") {
        return { items: [intent(), ...others], next_cursor: null };
      }
      return { items: [], next_cursor: null };
    });
    await standby.show();
    expect(root.querySelector("[data-sb-title-a]").textContent).toBe("条件が揃うのを、");
    expect(root.querySelector("[data-sb-title-b]").textContent).toBe("待っています。");
    expect(root.querySelector("[data-sb-badge]").textContent).toBe("待機中 · 2件");
    expect(root.querySelector("[data-sb-main]").textContent).toContain("軽く飲みたい".slice(0, 2) || "");
    expect(root.querySelectorAll(".sb-row").length).toBe(1);
    expect(onCounts).toHaveBeenCalledWith({ standby: 2 });
    standby.stop();
  });

  it("0件はほかに空状態・メインカード非表示", async () => {
    const { root, standby } = mountStandby(async () => ({
      items: [],
      next_cursor: null,
    }));
    await standby.show();
    expect(root.querySelector("[data-sb-main]").hidden).toBe(true);
    expect(root.querySelector(".sb-row-empty").textContent).toBe(
      "ほかに待機中の意思はありません。",
    );
    standby.stop();
  });

  it("着地状態(?deposited=1)は見出しとリードが切り替わる", async () => {
    window.location.hash = "#/standby?deposited=1";
    const { root, standby } = mountStandby(async () => ({
      items: [intent()],
      next_cursor: null,
    }));
    await standby.show();
    expect(root.querySelector("[data-sb-title-a]").textContent).toBe("預けました。");
    expect(root.querySelector("[data-sb-title-b]").textContent).toBe("あとは、待つだけ。");
    standby.stop();
  });

  it("残時間タイマーはdeadlineまでのHH:MM:SS(1秒更新)", async () => {
    vi.useFakeTimers();
    try {
      vi.setSystemTime(new Date("2026-10-10T23:44:20+09:00"));
      const { root, standby } = mountStandby(async () => ({
        items: [intent()],
        next_cursor: null,
      }));
      await standby.show();
      const timer = root.querySelector("[data-sb-timer]");
      expect(timer.textContent).toBe("00:14:40"); // 23:59まで880秒
      vi.advanceTimersByTime(1000);
      expect(timer.textContent).toBe("00:14:39");
      standby.stop();
    } finally {
      vi.useRealTimers();
    }
  });

  it("メインの取り下げ: dialog(dlg)→DELETE→取り下げ済み姿+トースト", async () => {
    const calls = [];
    let dialogActions = [];
    const { root, standby, chrome } = mountStandby(async (method, path) => {
      calls.push([method, path]);
      if (method === "DELETE") return null;
      if (path === "/v1/intents?status=active") {
        return { items: [intent()], next_cursor: null };
      }
      return { items: [], next_cursor: null };
    });
    chrome.openAppDialog.mockImplementation(({ actions }) => {
      dialogActions = actions;
    });
    await standby.show();
    root.querySelector("[data-sb-main] [data-sb-drop]").click();
    expect(chrome.openAppDialog).toHaveBeenCalledTimes(1);
    // やめておく=閉じるだけ(onClickを持たない・DELETEしない)
    expect(dialogActions[0].onClick).toBeUndefined();
    expect(calls.some(([m]) => m === "DELETE")).toBe(false);
    // 取り下げる=DELETE /v1/intents/{id}
    await dialogActions[1].onClick();
    expect(calls.some(([m, p]) => m === "DELETE" && p === `/v1/intents/${INTENT_ID}`)).toBe(true);
    expect(root.querySelector("[data-sb-main]").textContent).toContain("取り下げ済み");
    expect(root.querySelector("[data-sb-main]").textContent).toContain(
      "この条件は取り下げました",
    );
    expect(chrome.showToast).toHaveBeenCalledWith("取り下げました");
    standby.stop();
  });

  it("ほかの意思の取り下げ: DELETEで行が消え、メインカードはそのまま", async () => {
    const otherId = "bbbbbbbb-2222-4222-8222-222222222222";
    const others = [intent({ id: otherId, raw_text: "年収1,000万円以上なら転職を考える" })];
    let dialogActions = [];
    const { root, standby, chrome } = mountStandby(async (method, path) => {
      if (method === "DELETE") return null;
      if (path === "/v1/intents?status=active") {
        return { items: [intent(), ...others], next_cursor: null };
      }
      return { items: [], next_cursor: null };
    });
    chrome.openAppDialog.mockImplementation(({ actions }) => {
      dialogActions = actions;
    });
    await standby.show();
    root.querySelector(`[data-sb-drop="${otherId}"]`).click();
    await dialogActions[1].onClick();
    expect(root.querySelectorAll(".sb-row").length).toBe(0);
    expect(root.querySelector(".sb-row-empty")).not.toBeNull();
    // メインカードは取り下げ済みにならない
    expect(root.querySelector("[data-sb-main]").textContent).not.toContain("取り下げ済み");
    standby.stop();
  });

  it("候補があるときは右上トースト(見る→04)を1回だけ出す", async () => {
    const { root, standby, chrome, onCounts } = mountStandby(async (method, path) => {
      if (path === "/v1/intents?status=active") {
        return { items: [intent()], next_cursor: null };
      }
      return { items: [latch("proposed")], next_cursor: null };
    });
    await standby.show();
    expect(chrome.showToast).toHaveBeenCalledWith({
      message: "LATCH候補があります",
      href: `#/latches/${LATCH_ID}`,
    });
    expect(onCounts).toHaveBeenCalledWith({ proposals: 1, matched: 0 });
    // 再訪しても同じlatchで2回は出さない(セッション内1回)
    await standby.show();
    expect(chrome.showToast).toHaveBeenCalledTimes(1);
    standby.stop();
  });
});
