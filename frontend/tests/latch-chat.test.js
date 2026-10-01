import { afterEach, describe, expect, it, vi } from "vitest";
import { ApiError } from "../src/api/session.js";
import {
  CHAT_COMPLETED_TEXT,
  CHAT_UNAVAILABLE_TEXT,
} from "../src/latch/texts.js";
import { createChat } from "../src/latch/chat.js";

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
  created_at: "2026-10-01T20:00:00+09:00",
});

const mountChat = (latch = matchedLatch, callImpl) => {
  const client = { call: vi.fn(callImpl) };
  const mount = document.createElement("div");
  const chat = createChat({ client, latch, meId: ME, mount });
  return { client, mount, chat };
};

afterEach(() => {
  vi.useRealTimers();
});

describe("chat(デザイン§2.6)", () => {
  it("初回取得はnext_cursorが尽きるまで頁を進め昇順連結する", async () => {
    const pages = [
      { items: [message("m1", PEER, "1通目")], next_cursor: "c1" },
      { items: [message("m2", ME, "2通目"), message("m3", PEER, "3通目")], next_cursor: null },
    ];
    const { client, mount, chat } = mountChat(
      matchedLatch,
      async (method, path) => pages.shift(),
    );
    await chat.start();
    expect(client.call.mock.calls[0]).toEqual([
      "GET",
      `/v1/latches/${LATCH_ID}/messages?limit=100`,
    ]);
    expect(client.call.mock.calls[1]).toEqual([
      "GET",
      `/v1/latches/${LATCH_ID}/messages?limit=100&cursor=${encodeURIComponent("c1")}`,
    ]);
    const bodies = [...mount.querySelectorAll(".chat-body")].map((el) => el.textContent);
    expect(bodies).toEqual(["1通目", "2通目", "3通目"]); // 昇順
    chat.stop();
  });

  it("初回取得は5頁で打ち切り(6頁目を要求しない)", async () => {
    let calls = 0;
    const { client, chat } = mountChat(matchedLatch, async () => {
      calls += 1;
      return { items: [message(`m${calls}`, PEER, `msg${calls}`)], next_cursor: `c${calls}` };
    });
    await chat.start();
    expect(client.call).toHaveBeenCalledTimes(5); // 上限5頁
    chat.stop();
  });

  it("30秒間隔でポーリングしstopで停止する", async () => {
    vi.useFakeTimers();
    let calls = 0;
    const { client, chat } = mountChat(matchedLatch, async () => {
      calls += 1;
      return { items: [], next_cursor: null };
    });
    const started = chat.start();
    await vi.advanceTimersByTimeAsync(0); // start内の初回syncを完了させる
    await started;
    expect(calls).toBe(1);
    await vi.advanceTimersByTimeAsync(30_000);
    expect(calls).toBe(2);
    await vi.advanceTimersByTimeAsync(30_000);
    expect(calls).toBe(3);
    chat.stop();
    await vi.advanceTimersByTimeAsync(60_000);
    expect(calls).toBe(3); // stop後は増えない
  });

  it("送信はPOST後に再取得しローカル追記しない", async () => {
    let page = { items: [message("m1", PEER, "既存")], next_cursor: null };
    const posts = [];
    const { client, mount, chat } = mountChat(matchedLatch, async (method, path) => {
      if (method === "POST") {
        posts.push(path);
        return { message: message("m2", ME, "新着") };
      }
      page = { items: [...page.items, message("m2", ME, "新着")], next_cursor: null };
      return page;
    });
    await chat.start();
    mount.querySelector(".chat-input").value = "新着";
    await chat.send();
    expect(posts).toEqual([`/v1/latches/${LATCH_ID}/messages`]);
    const bodies = [...mount.querySelectorAll(".chat-body")].map((el) => el.textContent);
    expect(bodies).toEqual(["既存", "新着"]); // サーバ再取得の順序どおり
    chat.stop();
  });

  it("409 CHAT_READONLYで入力disabled+「このチャットは利用できません」", async () => {
    const { mount, chat } = mountChat(matchedLatch, async (method) => {
      if (method === "POST") throw new ApiError(409, "CHAT_READONLY", "readonly");
      return { items: [], next_cursor: null };
    });
    await chat.start();
    await chat.send();
    const input = mount.querySelector(".chat-input");
    const notice = mount.querySelector(".chat-notice");
    expect(input.disabled).toBe(true);
    expect(notice.textContent).toBe(CHAT_UNAVAILABLE_TEXT);
    expect(notice.hidden).toBe(false);
    chat.stop();
  });

  it("status=completedは最初から読取専用(閲覧のみ文言)", async () => {
    const completed = { ...matchedLatch, status: "completed" };
    const { mount, chat } = mountChat(completed, async () => ({
      items: [], next_cursor: null,
    }));
    await chat.start();
    expect(mount.querySelector(".chat-input").disabled).toBe(true);
    expect(mount.querySelector(".chat-notice").textContent).toBe(CHAT_COMPLETED_TEXT);
    chat.stop();
  });

  it("自分=右・相手=左・相手の送信者名はparticipantsから解決する", async () => {
    const { mount, chat } = mountChat(matchedLatch, async () => ({
      items: [
        message("m1", ME, "自分の発言"),
        message("m2", PEER, "相手の発言"),
      ],
      next_cursor: null,
    }));
    await chat.start();
    const rows = [...mount.querySelectorAll(".chat-message")];
    expect(rows[0].className).toContain("chat-mine");
    expect(rows[1].className).toContain("chat-theirs");
    expect(rows[1].querySelector(".chat-sender").textContent).toBe("相手");
    expect(rows[0].querySelector(".chat-sender")).toBeNull(); // 自分は名前なし
    chat.stop();
  });

  it("visibilitychangeのvisibleで1回取り直す", async () => {
    let calls = 0;
    const { client, chat } = mountChat(matchedLatch, async () => {
      calls += 1;
      return { items: [], next_cursor: null };
    });
    await chat.start();
    expect(calls).toBe(1);
    Object.defineProperty(document, "visibilityState", {
      configurable: true, value: "visible",
    });
    document.dispatchEvent(new Event("visibilitychange"));
    await Promise.resolve();
    await Promise.resolve();
    expect(calls).toBe(2);
    Object.defineProperty(document, "visibilityState", {
      configurable: true, value: "hidden",
    });
    document.dispatchEvent(new Event("visibilitychange"));
    await Promise.resolve();
    await Promise.resolve();
    expect(calls).toBe(2); // hiddenでは再取得しない
    chat.stop(); // listener掃除(以降のテストへ影響させない)
  });

  it("メッセージ本文はHTMLとして解釈されない(escapeHtml)", async () => {
    const { mount, chat } = mountChat(matchedLatch, async () => ({
      items: [message("m1", PEER, '<img src=x onerror="alert(1)">')],
      next_cursor: null,
    }));
    await chat.start();
    expect(mount.querySelector(".chat-body").innerHTML)
      .not.toContain("<img");
    expect(mount.querySelector(".chat-body").textContent)
      .toBe('<img src=x onerror="alert(1)">');
    chat.stop();
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
    const { mount, chat } = mountChat(matchedLatch, callImpl);
    await chat.start(); // 頁1〜5(m1..m500)
    await vi.advanceTimersByTimeAsync(30_000); // poll1: 頁6〜8で終端まで
    await vi.advanceTimersByTimeAsync(30_000); // poll2: 差分同期(順序崩壊の回帰対象)
    const bodies = [...mount.querySelectorAll(".chat-body")].map((el) => el.textContent);
    expect(bodies.length).toBe(500); // 表示は末尾500件(先頭省略)
    expect(bodies[0]).toBe("msg301");
    expect(bodies.at(-1)).toBe("msg800"); // 昇順が維持される
    chat.stop();
  });
});
