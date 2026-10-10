import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { createParseFlow, jstDateString } from "../src/intent/parseFlow.js";

beforeEach(() => {
  vi.useFakeTimers();
});
afterEach(() => {
  vi.useRealTimers();
});

const deferred = () => {
  let resolve;
  let reject;
  const promise = new Promise((res, rej) => {
    resolve = res;
    reject = rej;
  });
  return { promise, resolve, reject };
};

describe("createParseFlow(design §2.4)", () => {
  it("入力後1秒で送信する(即時ではない)", async () => {
    const send = vi.fn(async () => ({ structured_intent: {}, warnings: [] }));
    const flow = createParseFlow({ send, onResult: vi.fn(), onError: vi.fn() });
    flow.input("今日20時以降、天文館で軽く飲みたい。");
    expect(send).not.toHaveBeenCalled();
    await vi.advanceTimersByTimeAsync(1000);
    expect(send).toHaveBeenCalledTimes(1);
    flow.dispose();
  });

  it("再入力でdebounceが延長される(最後の1秒だけが有効)", async () => {
    const send = vi.fn(async () => ({ structured_intent: {}, warnings: [] }));
    const flow = createParseFlow({ send, onResult: vi.fn(), onError: vi.fn() });
    flow.input("今日20時以降、");
    await vi.advanceTimersByTimeAsync(700);
    flow.input("今日20時以降、天文館で軽く飲みたい。");
    await vi.advanceTimersByTimeAsync(700);
    expect(send).not.toHaveBeenCalled();
    await vi.advanceTimersByTimeAsync(300);
    expect(send).toHaveBeenCalledTimes(1);
    flow.dispose();
  });

  it("空テキスト・空白のみは送信しない", async () => {
    const send = vi.fn();
    const flow = createParseFlow({ send, onResult: vi.fn(), onError: vi.fn() });
    flow.input("");
    flow.input("   ");
    await vi.advanceTimersByTimeAsync(2000);
    expect(send).not.toHaveBeenCalled();
  });

  it("同一テキストの再送をしない(60req/分対策・Review Focus 5の前提)", async () => {
    const send = vi.fn(async () => ({ structured_intent: {}, warnings: [] }));
    const flow = createParseFlow({ send, onResult: vi.fn(), onError: vi.fn() });
    flow.input("今日20時以降、天文館で軽く飲みたい。");
    await vi.advanceTimersByTimeAsync(1000);
    flow.input("今日20時以降、天文館で軽く飲みたい。");
    await vi.advanceTimersByTimeAsync(2000);
    expect(send).toHaveBeenCalledTimes(1);
  });

  it("in-flight中の再入力で古いsendはabortされ古い応答は破棄(Review Focus 5)", async () => {
    const first = deferred();
    const second = deferred();
    const send = vi
      .fn()
      .mockImplementationOnce((_text, { signal }) => {
        signal.addEventListener("abort", () => first.reject(new DOMException("aborted", "AbortError")));
        return first.promise;
      })
      .mockImplementationOnce(() => second.promise);
    const onResult = vi.fn();
    const flow = createParseFlow({ send, onResult, onError: vi.fn() });

    flow.input("1本目のテキスト");
    await vi.advanceTimersByTimeAsync(1000);
    flow.input("2本目のテキスト");
    await vi.advanceTimersByTimeAsync(1000);

    // 1本目のsignalはabort済み
    expect(send.mock.calls[0][1].signal.aborted).toBe(true);
    // 古い応答が後からresolveしてもonResultは呼ばれない
    second.resolve({ structured_intent: { second: true }, warnings: [] });
    await vi.advanceTimersByTimeAsync(0);
    first.resolve({ structured_intent: { first: true }, warnings: [] });
    await Promise.resolve();
    expect(onResult).toHaveBeenCalledTimes(1);
    expect(onResult).toHaveBeenCalledWith({ structured_intent: { second: true }, warnings: [] });
    flow.dispose();
  });

  it("ApiErrorはonErrorへ(codeで分岐・確定値13)", async () => {
    const send = vi.fn(async () => {
      throw Object.assign(new Error("unavailable"), {
        name: "ApiError",
        status: 503,
        code: "LLM_UNAVAILABLE",
      });
    });
    const onError = vi.fn();
    const flow = createParseFlow({ send, onResult: vi.fn(), onError });
    flow.input("今日20時以降、天文館で軽く飲みたい。");
    await vi.advanceTimersByTimeAsync(1000);
    expect(onError).toHaveBeenCalledWith(
      expect.objectContaining({ code: "LLM_UNAVAILABLE" }),
    );
  });

  it("retry()は同一テキストでも再送する(503時のもう一度読み取る)", async () => {
    const send = vi.fn(async () => ({ structured_intent: {}, warnings: [] }));
    const flow = createParseFlow({ send, onResult: vi.fn(), onError: vi.fn() });
    flow.input("今日20時以降、天文館で軽く飲みたい。");
    await vi.advanceTimersByTimeAsync(1000);
    expect(send).toHaveBeenCalledTimes(1);
    flow.retry();
    await vi.advanceTimersByTimeAsync(0);
    expect(send).toHaveBeenCalledTimes(2);
    flow.dispose();
  });

  it("commit()はdebounceを打ち切って即時送信する(01のCTA「読み解く」)", async () => {
    const send = vi.fn(async () => ({ structured_intent: {}, warnings: [] }));
    const flow = createParseFlow({ send, onResult: vi.fn(), onError: vi.fn() });
    flow.input("今日20時以降、天文館で軽く飲みたい。");
    await flow.commit();
    expect(send).toHaveBeenCalledTimes(1); // 1秒を待たない
    await vi.advanceTimersByTimeAsync(2000);
    expect(send).toHaveBeenCalledTimes(1); // 二重送信しない
    flow.dispose();
  });

  it("commit()は同一テキスト(直前成功と同じ)なら送信せず即完了する", async () => {
    const send = vi.fn(async () => ({ structured_intent: {}, warnings: [] }));
    const flow = createParseFlow({ send, onResult: vi.fn(), onError: vi.fn() });
    flow.input("今日20時以降、天文館で軽く飲みたい。");
    await vi.advanceTimersByTimeAsync(1000);
    await flow.commit();
    expect(send).toHaveBeenCalledTimes(1);
    flow.dispose();
  });

  it("dispose()でタイマー停止", async () => {
    const send = vi.fn(async () => ({}));
    const flow = createParseFlow({ send, onResult: vi.fn(), onError: vi.fn() });
    flow.input("今日20時以降、天文館で軽く飲みたい。");
    flow.dispose();
    await vi.advanceTimersByTimeAsync(2000);
    expect(send).not.toHaveBeenCalled();
  });
});

describe("解析基準日(JST日跨ぎ — 移行計画§5.5)", () => {
  it("jstDateString: UTC日付とJST日付の境界", () => {
    // UTC 2026-10-08T16:00 = JST 2026-10-09T01:00
    expect(jstDateString(new Date("2026-10-08T16:00:00Z"))).toBe("2026-10-09");
    // UTC 2026-10-08T14:59 = JST 2026-10-08T23:59
    expect(jstDateString(new Date("2026-10-08T14:59:00Z"))).toBe("2026-10-08");
  });

  it("同文でもJST日を跨いだら再送する(23:59→00:00)", async () => {
    vi.useFakeTimers();
    try {
      const send = vi.fn(async () => ({ structured_intent: {}, warnings: [] }));
      const flow = createParseFlow({
        send,
        onResult: vi.fn(),
        onError: vi.fn(),
      });
      // JST 2026-10-08 23:59
      vi.setSystemTime(new Date("2026-10-08T14:59:00Z"));
      flow.input("明日の夜、天文館でごはん。");
      await vi.advanceTimersByTimeAsync(1000);
      expect(send).toHaveBeenCalledTimes(1);
      // 2分後 = JST 2026-10-09 00:01(同文のまま入力)
      vi.setSystemTime(new Date("2026-10-08T15:01:00Z"));
      flow.input("明日の夜、天文館でごはん。");
      await vi.advanceTimersByTimeAsync(1000);
      expect(send).toHaveBeenCalledTimes(2); // 同文でも基準日が変われば再送
      flow.dispose();
    } finally {
      vi.useRealTimers();
    }
  });

  it("同じ基準日内の同文は再送しない(従来挙動の維持)", async () => {
    vi.useFakeTimers();
    try {
      const send = vi.fn(async () => ({ structured_intent: {}, warnings: [] }));
      const flow = createParseFlow({
        send,
        onResult: vi.fn(),
        onError: vi.fn(),
      });
      vi.setSystemTime(new Date("2026-10-08T10:00:00Z"));
      flow.input("今夜、天文館でごはん。");
      await vi.advanceTimersByTimeAsync(1000);
      flow.input("今夜、天文館でごはん。");
      await vi.advanceTimersByTimeAsync(1000);
      expect(send).toHaveBeenCalledTimes(1);
      flow.dispose();
    } finally {
      vi.useRealTimers();
    }
  });
});
