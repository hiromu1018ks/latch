import { afterEach, describe, expect, it, vi } from "vitest";
import { act, cleanup, fireEvent, render, waitFor } from "@testing-library/react";
import { StandbyScreen } from "../src/latch/StandbyScreen.jsx";
import { AppDialog, ChromeProvider, ToastArea, useChrome } from "../src/ui/chrome.jsx";

// 旧standby.js(mainCardHtml等のHTML組立+createStandby配線)の試験を、
// Reactコンポーネント(StandbyScreen・UI v2 Phase 6)のRTLへ書き換えたもの。
// 意図=メインカード・取り下げ済みカード・オーブ・ほかの意思・下書きの行の
// 文言と構成、タイマー・取り下げdialog・候補トーストの挙動は踏襲する。
const INTENT_ID = "aaaaaaaa-1111-4111-8111-111111111111";
const LATCH_ID = "11111111-1111-4111-8111-111111111111";

// コンポーネントは内部で現在時刻を使うため、相対時間・期限行の文言試験は
// 現在時刻からの相対でfixtureを組む(12分前に預けた・1時間後に期限切れ)
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
  expires_at: new Date(Date.now() + 60 * 60 * 1000).toISOString(),
  created_at: new Date(Date.now() - 12 * 60 * 1000).toISOString(),
  updated_at: new Date(Date.now() - 12 * 60 * 1000).toISOString(),
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

const mountStandby = (callImpl, { hash } = {}) => {
  cleanup();
  if (hash != null) window.location.hash = hash;
  const client = { call: vi.fn(callImpl) };
  const chrome = { current: null };
  // chrome contextの値検査用(バッジ供給の試験で使う)
  const Probe = () => {
    chrome.current = useChrome();
    return null;
  };
  render(
    <ChromeProvider>
      <Probe />
      <StandbyScreen client={client} />
      {/* 取り下げdialog(chrome.openAppDialog)と候補トーストの受け皿 */}
      <AppDialog />
      <ToastArea />
    </ChromeProvider>,
  );
  return { client, chrome };
};

const mainCard = () => document.querySelector("[data-sb-main]");
const rows = () => [...document.querySelectorAll(".sb-row")];

afterEach(() => {
  cleanup();
  window.location.hash = "";
});

describe("メイン条件カード(プロトタイプ03)", () => {
  it("pill・原文引用・条件タグ・残時間タイマー・条件を直す・取り下げるを組む", async () => {
    mountStandby(async (method, path) => {
      if (path === "/v1/intents?status=active") return { items: [intent()], next_cursor: null };
      if (path === "/v1/intents?status=draft") return { items: [], next_cursor: null };
      return { items: [], next_cursor: null };
    });
    await waitFor(() => expect(mainCard().textContent).toContain("飲み · 12分前に預けた"));
    const text = mainCard().textContent;
    expect(text).toContain("待機中");
    expect(text).toContain("「今日20時以降、天文館で2〜4人、予算5,000円までなら軽く飲みたい」");
    expect([...mainCard().querySelectorAll(".sb-tag")].map((t) => t.textContent)).toEqual([
      "20:00〜",
      "天文館",
      "2〜4人",
      "〜¥5,000",
    ]);
    expect(mainCard().querySelector('[role="timer"]')).not.toBeNull(); // 1秒更新はRemainTimer側
    expect(text).toMatch(/\d\d:\d\d の取り下げまで/);
    expect(mainCard().querySelector(".sb-edit").getAttribute("href")).toBe(
      `#/confirm?intent=${INTENT_ID}`,
    ); // 条件を直す=02編集
    expect(text).toContain("条件を直す");
    expect(text).toContain("取り下げる");
    expect(mainCard().querySelector('[aria-haspopup="dialog"]')).not.toBeNull();
  });

  it("期限なしはタイマーを出さず「期限なし」", async () => {
    mountStandby(async (method, path) => {
      if (path === "/v1/intents?status=active") {
        return { items: [intent({ expires_at: null })], next_cursor: null };
      }
      if (path === "/v1/intents?status=draft") return { items: [], next_cursor: null };
      return { items: [], next_cursor: null };
    });
    await waitFor(() => expect(mainCard().textContent).toContain("期限なし"));
    expect(mainCard().querySelector('[role="timer"]')).toBeNull();
  });

  it("raw_textはテキストノードとして組まれる(XSS対策)", async () => {
    mountStandby(async (method, path) => {
      if (path === "/v1/intents?status=active") {
        return { items: [intent({ raw_text: '<img src=x onerror=alert(1)>' })], next_cursor: null };
      }
      if (path === "/v1/intents?status=draft") return { items: [], next_cursor: null };
      return { items: [], next_cursor: null };
    });
    await waitFor(() => expect(mainCard().querySelector(".sb-main-quote")).not.toBeNull());
    expect(mainCard().querySelector("img")).toBeNull();
    expect(mainCard().querySelector(".sb-main-quote").textContent).toBe(
      "「<img src=x onerror=alert(1)>」",
    );
  });
});

describe("取り下げ済み状態(プロトタイプ03のmainGone)", () => {
  it("取り下げ済みpill・案内文・もう一度預ける", async () => {
    let dropResolve;
    mountStandby(async (method, path) => {
      if (method === "DELETE") {
        return new Promise((resolve) => {
          dropResolve = resolve; // 取り下げ中の状態は試験で直接操作しない
        }).then(() => null);
      }
      if (path === "/v1/intents?status=active") return { items: [intent()], next_cursor: null };
      if (path === "/v1/intents?status=draft") return { items: [], next_cursor: null };
      return { items: [], next_cursor: null };
    });
    await waitFor(() => expect(mainCard().textContent).toContain("飲み"));
    // メインの取り下げ: dialog→取り下げる→DELETE(完了待ちで取り下げ済み姿へ)
    fireEvent.click(mainCard().querySelector("button[aria-haspopup='dialog']"));
    await waitFor(() => {
      expect(document.querySelector("#appDialog h2").textContent).toBe("この条件を取り下げますか？");
    });
    const buttons = [...document.querySelectorAll("#appDialog button")];
    expect(buttons.map((b) => b.textContent)).toEqual(["やめておく", "取り下げる"]);
    buttons[1].click(); // DELETE(保留中)
    await waitFor(() => {
      // DELETE完了前はメインカードのまま(REACTではstate更新は応答後)
      expect(document.querySelector("#appDialog")).not.toBeNull();
    });
    await act(async () => {
      dropResolve(null);
    });
    await waitFor(() => {
      const text = mainCard().textContent;
      expect(text).toContain("取り下げ済み");
      expect(text).toContain("この条件は取り下げました。もう誰とも照らし合わされません。");
      expect(document.querySelector("#appDialog h2")).toBeNull(); // dialogは閉じる
    });
    expect(mainCard().querySelector("a.sb-again").getAttribute("href")).toBe("#/");
    expect(mainCard().textContent).toContain("もう一度預ける");
  });

  it("取り下げでchrome countsのstandbyも減る(ヘッダー/タブバーのバッジ追従)", async () => {
    // phase 7実機E2Eで検出: 画面内のバッジは減るがchrome側の供給が更新されず、
    // ヘッダーの「待機中」バッジが実件数より多く残る
    const { chrome } = mountStandby(async (method, path) => {
      if (method === "DELETE") return null;
      if (path === "/v1/intents?status=active") {
        return { items: [intent()], next_cursor: null };
      }
      if (path === "/v1/intents?status=draft") return { items: [], next_cursor: null };
      return { items: [], next_cursor: null };
    });
    await waitFor(() => expect(chrome.current).not.toBeNull());
    await waitFor(() => expect(chrome.current.counts.standby).toBe(1));
    fireEvent.click(mainCard().querySelector("button[aria-haspopup='dialog']"));
    const buttons = [...document.querySelectorAll("#appDialog button")];
    buttons[1].click(); // 取り下げる=DELETE
    await waitFor(() => {
      expect(chrome.current.counts.standby).toBe(0);
    });
  });
});

describe("ほかに待機中の意思・下書きの行", () => {
  it("原文・meta(種別 · 相対時間 · 期限)・直す・取り下げる", async () => {
    mountStandby(async (method, path) => {
      if (path === "/v1/intents?status=active") {
        return { items: [intent(), intent({ id: "bbbbbbbb-2222-4222-8222-222222222222", raw_text: "年収1,000万円以上なら転職を考える" })], next_cursor: null };
      }
      if (path === "/v1/intents?status=draft") return { items: [], next_cursor: null };
      return { items: [], next_cursor: null };
    });
    await waitFor(() => expect(rows().length).toBe(1));
    const row = rows()[0];
    expect(row.textContent).toContain("「年収1,000万円以上なら転職を考える」");
    expect(row.textContent).toContain("飲み · 12分前に預けた · ");
    expect(row.textContent).toContain("まで有効"); // 期限行(当日は時刻を出し先日は日付まで)
    expect(row.querySelector(".sb-row-edit").getAttribute("href")).toBe(
      "#/confirm?intent=bbbbbbbb-2222-4222-8222-222222222222",
    );
    expect(row.querySelector(".sb-row-edit").getAttribute("aria-label")).toBe(
      "年収1,000万円以上なら転職を考えるを直す",
    );
    expect(row.querySelector("button[aria-haspopup='dialog']").getAttribute("aria-label")).toBe(
      "年収1,000万円以上なら転職を考えるを取り下げる",
    );
    // 罫線(data-border)は「ほか」の一覧の2行目以降に付く(下書きの試験で確認)
  });

  it("オーブ(2アーク+rippleと条件チップ)がメインと一緒に置かれる", async () => {
    mountStandby(async (method, path) => {
      if (path === "/v1/intents?status=active") return { items: [intent()], next_cursor: null };
      if (path === "/v1/intents?status=draft") return { items: [], next_cursor: null };
      return { items: [], next_cursor: null };
    });
    await waitFor(() => expect(document.querySelector("[data-sb-orb] .orb-svg")).not.toBeNull());
    const orb = document.querySelector("[data-sb-orb]");
    expect(orb.querySelector(".arcL")).not.toBeNull();
    expect(orb.querySelector(".arcR")).not.toBeNull();
    expect(orb.querySelector(".ripple.d3")).not.toBeNull();
    const chips = [...orb.querySelectorAll(".orb-chip")].map((c) => c.textContent);
    expect(chips).toEqual(["20:00〜", "天文館", "2〜4人", "〜¥5,000"]);
  });

  it("下書き(D-13): 原文・meta(種別 · 相対時間)・下書きpill・直して預ける・取り下げる", async () => {
    mountStandby(async (method, path) => {
      if (path === "/v1/intents?status=active") return { items: [intent()], next_cursor: null };
      if (path === "/v1/intents?status=draft") {
        return {
          items: [
            intent({
              id: "dddddddd-4444-4444-8444-444444444444",
              status: "draft",
              expires_at: null,
              raw_text: "この車が190万円以上なら売ってもいい",
            }),
            intent({
              id: "eeeeeeee-4444-4444-8444-444444444444",
              status: "draft",
              expires_at: null,
              raw_text: "4人集まるなら参加したい",
            }),
          ],
          next_cursor: null,
        };
      }
      return { items: [], next_cursor: null };
    });
    await waitFor(() => {
      expect(document.querySelector("[data-sb-drafts-section]").hidden).toBe(false);
    });
    const draftRows = [...document.querySelectorAll("[data-sb-drafts] .sb-row")];
    expect(draftRows.length).toBe(2);
    const row = draftRows[0];
    expect(row.textContent).toContain("「この車が190万円以上なら売ってもいい」");
    expect(row.textContent).toContain("飲み · 12分前に保存");
    expect(row.textContent).not.toContain("まで有効"); // 下書きに期限行は出さない
    expect(row.querySelector(".sb-pill").textContent).toBe("下書き");
    expect(row.querySelector(".sb-row-edit").getAttribute("href")).toBe(
      "#/confirm?intent=dddddddd-4444-4444-8444-444444444444",
    );
    expect(row.querySelector(".sb-row-edit").getAttribute("aria-label")).toBe(
      "この車が190万円以上なら売ってもいいを直して預ける",
    );
    expect(row.querySelector("button[aria-haspopup='dialog']")).not.toBeNull();
    expect(draftRows[1].getAttribute("data-border")).toBe("true"); // 2行目以降は罫線つき
  });

  it("下書き0件はセクションごと隠す(空状態の規定なし)", async () => {
    mountStandby(async (method, path) => {
      if (path === "/v1/intents?status=active") return { items: [intent()], next_cursor: null };
      if (path === "/v1/intents?status=draft") return { items: [], next_cursor: null };
      return { items: [], next_cursor: null };
    });
    await waitFor(() => expect(mainCard().textContent).toContain("飲み"));
    expect(document.querySelector("[data-sb-drafts-section]").hidden).toBe(true);
  });
});

describe("03の配線(一覧・着地・タイマー・取り下げ・トースト)", () => {
  it("showは一覧を描き、先頭がメイン・残りがほかの意思・空状態", async () => {
    mountStandby(async (method, path) => {
      if (path === "/v1/intents?status=active") {
        return { items: [intent(), intent({ id: "bbbbbbbb-2222-4222-8222-222222222222" })], next_cursor: null };
      }
      if (path === "/v1/intents?status=draft") return { items: [], next_cursor: null };
      return { items: [], next_cursor: null };
    });
    await waitFor(() => expect(rows().length).toBe(1));
    expect(document.querySelector("[data-sb-title-a]").textContent).toBe("条件が揃うのを、");
    expect(document.querySelector("[data-sb-title-b]").textContent).toBe("待っています。");
    expect(document.querySelector("[data-sb-badge]").textContent).toBe("待機中 · 2件");
    expect(document.querySelector(".sb-row-empty")).toBeNull();
  });

  it("0件はほかに空状態・メインカード非表示", async () => {
    mountStandby(async () => ({ items: [], next_cursor: null }));
    await waitFor(() => {
      expect(mainCard().hidden).toBe(true);
      expect(document.querySelector(".sb-row-empty").textContent).toBe(
        "ほかに待機中の意思はありません。",
      );
    });
  });

  it("着地状態(?deposited=1)は見出しとリードが切り替わる", async () => {
    mountStandby(async (method, path) => {
      if (path === "/v1/intents?status=active") return { items: [intent()], next_cursor: null };
      if (path === "/v1/intents?status=draft") return { items: [], next_cursor: null };
      return { items: [], next_cursor: null };
    }, { hash: "#/standby?deposited=1" });
    await waitFor(() => {
      expect(document.querySelector("[data-sb-title-a]").textContent).toBe("預けました。");
      expect(document.querySelector("[data-sb-title-b]").textContent).toBe("あとは、待つだけ。");
    });
  });

  it("残時間タイマーはdeadlineまでのHH:MM:SS(1秒更新)", async () => {
    vi.useFakeTimers();
    try {
      vi.setSystemTime(new Date("2026-10-10T23:44:20+09:00"));
      const fixed = intent({ expires_at: "2026-10-10T23:59:00+09:00" });
      mountStandby(async (method, path) => {
        if (path === "/v1/intents?status=active") return { items: [fixed], next_cursor: null };
        if (path === "/v1/intents?status=draft") return { items: [], next_cursor: null };
        return { items: [], next_cursor: null };
      });
      await act(async () => { await vi.advanceTimersByTimeAsync(0); });
      const timer = document.querySelector("[data-sb-timer]");
      expect(timer.textContent).toBe("00:14:40"); // 23:59まで880秒
      await act(async () => { vi.advanceTimersByTime(1000); });
      expect(timer.textContent).toBe("00:14:39");
    } finally {
      vi.useRealTimers();
    }
  });

  it("ほかの意思の取り下げ: DELETEで行が消え、メインカードはそのまま", async () => {
    const otherId = "bbbbbbbb-2222-4222-8222-222222222222";
    const others = [intent({ id: otherId, raw_text: "年収1,000万円以上なら転職を考える" })];
    mountStandby(async (method, path) => {
      if (method === "DELETE") return null;
      if (path === "/v1/intents?status=active") {
        return { items: [intent(), ...others], next_cursor: null };
      }
      if (path === "/v1/intents?status=draft") return { items: [], next_cursor: null };
      return { items: [], next_cursor: null };
    });
    await waitFor(() => expect(rows().length).toBe(1));
    fireEvent.click(rows()[0].querySelector("button[aria-haspopup='dialog']"));
    await waitFor(() => {
      expect(document.querySelector("#appDialog h2").textContent).toBe("この条件を取り下げますか？");
    });
    const buttons = [...document.querySelectorAll("#appDialog button")];
    // やめておく=閉じるだけ(DELETEしない)
    buttons[0].click();
    await act(async () => {});
    await waitFor(() => expect(document.querySelector("#appDialog h2")).toBeNull());
    expect(rows().length).toBe(1); // 行は消えない
    // 今度は取り下げる=DELETE /v1/intents/{id}
    fireEvent.click(rows()[0].querySelector("button[aria-haspopup='dialog']"));
    await waitFor(() => expect(document.querySelector("#appDialog h2")).not.toBeNull());
    const confirmButtons = [...document.querySelectorAll("#appDialog button")];
    fireEvent.click(confirmButtons[1]);
    await waitFor(() => expect(rows().length).toBe(0));
    expect(document.querySelector(".sb-row-empty")).not.toBeNull();
    // メインカードは取り下げ済みにならない
    expect(mainCard().textContent).not.toContain("取り下げ済み");
  });

  it("候補があるときは右上トースト(見る→04)を1回だけ出す", async () => {
    mountStandby(async (method, path) => {
      if (path === "/v1/intents?status=active") return { items: [intent()], next_cursor: null };
      if (path === "/v1/intents?status=draft") return { items: [], next_cursor: null };
      return { items: [latch("proposed")], next_cursor: null };
    });
    await waitFor(() => {
      const link = document.querySelector("a.toast-link");
      expect(link).not.toBeNull();
      expect(link.getAttribute("href")).toBe(`#/latches/${LATCH_ID}`);
      expect(document.querySelector(".toast-msg").textContent).toBe("LATCH候補があります");
    });
    // 再訪(remount)しても同じlatchで2回は出さない(セッション内1回)
    cleanup();
    mountStandby(async (method, path) => {
      if (path === "/v1/intents?status=active") return { items: [intent()], next_cursor: null };
      if (path === "/v1/intents?status=draft") return { items: [], next_cursor: null };
      return { items: [latch("proposed")], next_cursor: null };
    });
    await waitFor(() => expect(mainCard().textContent).toContain("飲み"));
    expect(document.querySelector("a.toast-link")).toBeNull();
  });
});
