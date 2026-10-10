import { afterEach, describe, expect, it, vi } from "vitest";
import { act, cleanup, fireEvent, render, waitFor } from "@testing-library/react";
import { ApiError } from "../src/api/session.js";
import {
  CHAT_COMPLETED_TEXT,
  CHAT_LEFT_PLACEHOLDER_TEXT,
  CHAT_OPENING_TEXT,
  CHAT_UNAVAILABLE_TEXT,
} from "../src/latch/texts.js";
import { Chat } from "../src/latch/Chat.jsx";

// 旧createChat(素のDOM配線)の試験を、Reactコンポーネント(Chat・UI v2 Phase 6)
// のRTLへ書き換えたもの。意図=ヘッダー・role=log・ページング同期・30秒ポーリング・
// 送信の再取得・読み取り専用・システム行・500件超の順序は踏襲する。
const LATCH_ID = "11111111-1111-4111-8111-111111111111";
const ME = "22222222-2222-4222-8222-222222222222";
const PEER = "33333333-3333-4333-8333-333333333333";

const matchedLatch = {
  id: LATCH_ID, status: "matched", proposal: {}, is_group: false,
  my_response: null, remaining_responses: 0,
  participants: [
    { user_id: ME, display_name: "自分", profile: {} },
    { user_id: PEER, display_name: "相手", profile: {} },
  ],
};
const message = (id, senderId, body) => ({
  id, latch_id: LATCH_ID, sender_id: senderId, body,
  created_at: "2026-10-01T20:05:00+09:00",
});

const mountChat = (latch = matchedLatch, callImpl) => {
  const client = { call: vi.fn(callImpl) };
  render(<Chat client={client} latch={latch} meId={ME} />);
  return { client };
};

const bubbles = () => [...document.querySelectorAll(".lt-bubble")].map((el) => el.textContent);

afterEach(() => {
  cleanup();
  vi.useRealTimers();
});

describe("chat 05再構成(プロトタイプ05)", () => {
  it("ヘッダー: 「N人のチャット」+条件サブ+アバタースタック(自分=me様式)", async () => {
    const latched = {
      ...matchedLatch,
      proposal: { headcount: 3 },
      time_summary: "2026-10-10 20:30",
      area_name: "天文館周辺",
      participants: [
        { user_id: PEER, display_name: "ゆうき", profile: {} },
        { user_id: ME, display_name: "自分", profile: {} },
      ],
    };
    mountChat(latched, async () => ({
      items: [], next_cursor: null,
    }));
    await waitFor(() => expect(document.querySelector(".lt-chat-title").textContent).toMatch(/の3人$/));
    expect(document.querySelector(".lt-chat-sub").textContent)
      .toBe("20:30 · 天文館周辺");
    const stack = [...document.querySelectorAll(".lt-avatar-stack .lt-avatar")];
    expect(stack.map((el) => el.textContent)).toEqual(["ゆ", "自"]);
    expect(stack[1].className).toContain("me"); // 自分=末尾・me様式
  });

  it("role=log+開始システム行(acc pill)で会話が始まる", async () => {
    mountChat(matchedLatch, async () => ({
      items: [], next_cursor: null,
    }));
    await waitFor(() => expect(document.querySelector(".lt-log")).not.toBeNull());
    expect(document.querySelector(".lt-log").getAttribute("role")).toBe("log");
    expect(document.querySelector(".lt-opening").textContent).toContain(CHAT_OPENING_TEXT);
  });

  it("初回取得はnext_cursorが尽きるまで頁を進め昇順連結する", async () => {
    const pages = [
      { items: [message("m1", PEER, "1通目")], next_cursor: "c1" },
      { items: [message("m2", ME, "2通目"), message("m3", PEER, "3通目")], next_cursor: null },
    ];
    const { client } = mountChat(matchedLatch, async (method, path) => pages.shift());
    await waitFor(() => expect(bubbles().length).toBe(3));
    expect(client.call.mock.calls[0]).toEqual([
      "GET",
      `/v1/latches/${LATCH_ID}/messages?limit=100`,
    ]);
    expect(client.call.mock.calls[1]).toEqual([
      "GET",
      `/v1/latches/${LATCH_ID}/messages?limit=100&cursor=${encodeURIComponent("c1")}`,
    ]);
    expect(bubbles()).toEqual(["1通目", "2通目", "3通目"]); // 昇順
  });

  it("初回取得は5頁で打ち切り(6頁目を要求しない)", async () => {
    let calls = 0;
    const { client } = mountChat(matchedLatch, async () => {
      calls += 1;
      return { items: [message(`m${calls}`, PEER, `msg${calls}`)], next_cursor: `c${calls}` };
    });
    await waitFor(() => expect(client.call).toHaveBeenCalledTimes(5)); // 上限5頁
  });

  it("30秒間隔でポーリングしunmountで停止する", async () => {
    vi.useFakeTimers();
    let calls = 0;
    const { client, unmount } = (() => {
      const client = { call: vi.fn(async () => {
        calls += 1;
        return { items: [], next_cursor: null };
      }) };
      const utils = render(<Chat client={client} latch={matchedLatch} meId={ME} />);
      return { client, unmount: utils.unmount };
    })();
    await act(async () => { await vi.advanceTimersByTimeAsync(0); }); // 初回sync
    expect(calls).toBe(1);
    await act(async () => { await vi.advanceTimersByTimeAsync(30_000); });
    expect(calls).toBe(2);
    await act(async () => { await vi.advanceTimersByTimeAsync(30_000); });
    expect(calls).toBe(3);
    unmount();
    await act(async () => { await vi.advanceTimersByTimeAsync(60_000); });
    expect(calls).toBe(3); // unmount後は増えない
  });

  it("送信はPOST後に再取得しローカル追記しない(入力はクリア)", async () => {
    let page = { items: [message("m1", PEER, "既存")], next_cursor: null };
    const posts = [];
    const { client } = mountChat(matchedLatch, async (method, path) => {
      if (method === "POST") {
        posts.push(path);
        return { message: message("m2", ME, "新着") };
      }
      page = { items: [...page.items, message("m2", ME, "新着")], next_cursor: null };
      return page;
    });
    await waitFor(() => expect(bubbles().length).toBeGreaterThanOrEqual(1)); // 初期sync
    const input = document.querySelector("#msg");
    fireEvent.change(input, { target: { value: "新着" } });
    expect(document.querySelector(".lt-send").disabled).toBe(false);
    fireEvent.submit(document.querySelector("form.lt-chat-form"));
    await waitFor(() => {
      expect(posts).toEqual([`/v1/latches/${LATCH_ID}/messages`]);
    });
    await waitFor(() => {
      expect(document.querySelector("#msg").value).toBe(""); // 送信後クリア
      expect(bubbles()).toEqual(["既存", "新着"]); // サーバ再取得の順序どおり
    });
  });

  it("空入力は送信ボタン無効・1字入力で有効になる", async () => {
    mountChat(matchedLatch, async () => ({
      items: [], next_cursor: null,
    }));
    await waitFor(() => expect(document.querySelector("#msg")).not.toBeNull());
    const input = document.querySelector("#msg");
    const send = document.querySelector(".lt-send");
    expect(send.disabled).toBe(true);
    fireEvent.change(input, { target: { value: "やあ" } });
    expect(send.disabled).toBe(false);
  });

  it("自分=右(名前なし・あなた)+相手=左(アバター+名前+時間)", async () => {
    mountChat(matchedLatch, async () => ({
      items: [
        message("m1", ME, "自分の発言"),
        message("m2", PEER, "相手の発言"),
      ],
      next_cursor: null,
    }));
    await waitFor(() => expect(bubbles().length).toBe(2));
    const rows = [...document.querySelectorAll(".lt-msg")];
    expect(rows[0].className).toContain("mine");
    expect(rows[0].querySelector(".lt-msg-meta").textContent).toContain("あなた · ");
    expect(rows[1].className).toContain("theirs");
    expect(rows[1].querySelector(".lt-msg-meta").textContent).toContain("相手 · ");
    expect(rows[1].querySelector(".lt-avatar--msg").textContent).toBe("相"); // イニシャル
  });

  it("システム行(sender_id=NULL)は中央のdng pillでbodyのまま出す(D-08)", async () => {
    mountChat(matchedLatch, async () => ({
      items: [message("m1", null, "あなたが参加を取り消しました")],
      next_cursor: null,
    }));
    await waitFor(() => expect(document.querySelector(".lt-system")).not.toBeNull());
    const system = document.querySelector(".lt-system");
    expect(system.textContent).toBe("あなたが参加を取り消しました");
    expect(document.querySelector(".lt-bubble")).toBeNull(); // bubble扱いにしない
  });

  it("409 CHAT_READONLYで入力disabled+「このチャットは利用できません」", async () => {
    const { client } = mountChat(matchedLatch, async (method) => {
      if (method === "POST") throw new ApiError(409, "CHAT_READONLY", "readonly");
      return { items: [], next_cursor: null };
    });
    await waitFor(() => expect(document.querySelector("#msg")).not.toBeNull());
    fireEvent.change(document.querySelector("#msg"), { target: { value: "x" } });
    fireEvent.submit(document.querySelector("form.lt-chat-form"));
    await waitFor(() => expect(client.call).toHaveBeenCalledTimes(2));
    const input = document.querySelector("#msg");
    const notice = document.querySelector(".chat-notice");
    expect(input.disabled).toBe(true);
    expect(notice.textContent).toBe(CHAT_UNAVAILABLE_TEXT);
    expect(notice.hidden).toBe(false);
  });

  it("status=completedは最初から読取専用(閲覧のみ文言)", async () => {
    const completed = { ...matchedLatch, status: "completed" };
    mountChat(completed, async () => ({
      items: [], next_cursor: null,
    }));
    await waitFor(() => expect(document.querySelector("#msg")).not.toBeNull());
    expect(document.querySelector("#msg").disabled).toBe(true);
    expect(document.querySelector(".lt-send").disabled).toBe(true);
    expect(document.querySelector(".chat-notice").textContent).toBe(CHAT_COMPLETED_TEXT);
  });

  it("left(D-08): 取り消し後は入力disabled+専用placeholder", async () => {
    cleanup();
    const client = { call: vi.fn(async () => ({ items: [], next_cursor: null })) };
    const utils = render(<Chat client={client} latch={matchedLatch} meId={ME} left />);
    await waitFor(() => expect(document.querySelector("#msg")).not.toBeNull());
    const input = document.querySelector("#msg");
    expect(input.disabled).toBe(true);
    expect(input.placeholder).toBe(CHAT_LEFT_PLACEHOLDER_TEXT);
    expect(document.querySelector(".lt-send").disabled).toBe(true);
    utils.unmount();
  });

  it("visibilitychangeのvisibleで1回取り直す", async () => {
    let calls = 0;
    const { client } = mountChat(matchedLatch, async () => {
      calls += 1;
      return { items: [], next_cursor: null };
    });
    await waitFor(() => expect(calls).toBe(1));
    Object.defineProperty(document, "visibilityState", {
      configurable: true, value: "visible",
    });
    await act(async () => {
      document.dispatchEvent(new Event("visibilitychange"));
    });
    await waitFor(() => expect(calls).toBe(2));
    Object.defineProperty(document, "visibilityState", {
      configurable: true, value: "hidden",
    });
    await act(async () => {
      document.dispatchEvent(new Event("visibilitychange"));
    });
    await new Promise((resolve) => setTimeout(resolve, 0));
    expect(calls).toBe(2); // hiddenでは再取得しない
  });

  it("メッセージ本文はHTMLとして解釈されない(テキストノード)", async () => {
    mountChat(matchedLatch, async () => ({
      items: [message("m1", PEER, '<img src=x onerror="alert(1)">')],
      next_cursor: null,
    }));
    await waitFor(() => expect(bubbles().length).toBe(1));
    expect(document.querySelector(".lt-bubble").innerHTML)
      .not.toContain("<img");
    expect(document.querySelector(".lt-bubble").textContent)
      .toBe('<img src=x onerror="alert(1)">');
  });

  it("500件超の会話でも順序が崩れない(先頭省略は表示のみ・差分同期は最終頁cursorから)", async () => {
    vi.useFakeTimers();
    const PAGE = 100;
    const TOTAL = 800;
    // cursor=cN は「頁Nの次(=頁N+1)」の位置を指す不透明文字列のモック
    const callImpl = async (method, path) => {
      const m = /cursor=c(\d+)/.exec(decodeURIComponent(path));
      const page = m ? Number(m[1]) + 1 : 1;
      return {
        items: Array.from({ length: PAGE }, (_, j) =>
          message(`m${(page - 1) * PAGE + j + 1}`, PEER, `msg${(page - 1) * PAGE + j + 1}`)),
        next_cursor: page * PAGE < TOTAL ? `c${page}` : null,
      };
    };
    const client = { call: vi.fn(callImpl) };
    const utils = render(<Chat client={client} latch={matchedLatch} meId={ME} />);
    await act(async () => { await vi.advanceTimersByTimeAsync(0); }); // 頁1〜5(m1..m500)
    await act(async () => { await vi.advanceTimersByTimeAsync(30_000); }); // poll1: 頁6〜8で終端まで
    await act(async () => { await vi.advanceTimersByTimeAsync(30_000); }); // poll2: 差分同期(順序崩壊の回帰対象)
    const texts = [...document.querySelectorAll(".lt-bubble")].map((el) => el.textContent);
    expect(texts.length).toBe(500); // 表示は末尾500件(先頭省略)
    expect(texts[0]).toBe("msg301");
    expect(texts.at(-1)).toBe("msg800"); // 昇順が維持される
    utils.unmount();
  });
});
