import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { createParseFlow } from "../src/intent/parseFlow.js";

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

  it("dispose()でタイマー停止", async () => {
    const send = vi.fn(async () => ({}));
    const flow = createParseFlow({ send, onResult: vi.fn(), onError: vi.fn() });
    flow.input("今日20時以降、天文館で軽く飲みたい。");
    flow.dispose();
    await vi.advanceTimersByTimeAsync(2000);
    expect(send).not.toHaveBeenCalled();
  });
});
