import { beforeEach, describe, expect, it, vi } from "vitest";
import { createClient } from "../src/api/client.js";
import { createSession } from "../src/api/session.js";

const memoryStorage = () => {
  const store = new Map();
  return {
    getItem: (k) => (store.has(k) ? store.get(k) : null),
    setItem: (k, v) => store.set(k, String(v)),
    removeItem: (k) => store.delete(k),
  };
};

const makeSession = () =>
  createSession({ storage: { session: memoryStorage(), local: memoryStorage() } });

const jsonResponse = (status, body) =>
  new Response(JSON.stringify(body), { status });

beforeEach(() => {
  vi.restoreAllMocks();
});

describe("createClient.call", () => {
  it("Authorizationヘッダー付きでリクエストし200のbodyを返す", async () => {
    const session = makeSession();
    session.save({ access_token: "at-1", refresh_token: "rt-1" });
    const fetchImpl = vi.fn(async () =>
      jsonResponse(200, { options: [], default_index: null }),
    );
    const client = createClient({ session, fetchImpl });
    const data = await client.call("GET", "/v1/intents/expiry-options");
    expect(data).toEqual({ options: [], default_index: null });
    const [url, init] = fetchImpl.mock.calls[0];
    expect(url).toBe("/v1/intents/expiry-options");
    expect(init.headers.Authorization).toBe("Bearer at-1");
  });

  it("bodyはJSON化されContent-Typeが付く", async () => {
    const session = makeSession();
    session.save({ access_token: "at-1", refresh_token: "rt-1" });
    const fetchImpl = vi.fn(async () => jsonResponse(200, { ok: true }));
    const client = createClient({ session, fetchImpl });
    await client.call("POST", "/v1/intents/parse", { body: { text: "…" } });
    const [, init] = fetchImpl.mock.calls[0];
    expect(init.headers["Content-Type"]).toBe("application/json");
    expect(JSON.parse(init.body)).toEqual({ text: "…" });
  });

  it("エラー時はenvelopeのcodeでApiError(messageは参考・確定値13)", async () => {
    const session = makeSession();
    session.save({ access_token: "at-1", refresh_token: "rt-1" });
    const fetchImpl = vi.fn(async () =>
      jsonResponse(422, {
        error: { code: "VALIDATION_ERROR", message: "time.start is in the past", details: null },
      }),
    );
    const client = createClient({ session, fetchImpl });
    await expect(client.call("POST", "/v1/intents")).rejects.toMatchObject({
      status: 422,
      code: "VALIDATION_ERROR",
    });
  });

  it("401→refresh成功→元リクエストを再送する(1回のみ)", async () => {
    const session = makeSession();
    session.save({ access_token: "expired", refresh_token: "rt-1" });
    const fetchImpl = vi
      .fn()
      .mockResolvedValueOnce(jsonResponse(401, { error: { code: "UNAUTHENTICATED", message: "x", details: null } }))
      .mockResolvedValueOnce(jsonResponse(200, { access_token: "at-2", refresh_token: "rt-2" }))
      .mockResolvedValueOnce(jsonResponse(200, { options: [], default_index: null }));
    const client = createClient({ session, fetchImpl });
    const data = await client.call("GET", "/v1/intents/expiry-options");
    expect(data).toEqual({ options: [], default_index: null });
    expect(fetchImpl.mock.calls[0][1].headers.Authorization).toBe("Bearer expired");
    expect(fetchImpl.mock.calls[1][0]).toBe("/v1/auth/refresh");
    expect(fetchImpl.mock.calls[2][1].headers.Authorization).toBe("Bearer at-2");
    expect(session.current().refreshToken).toBe("rt-2"); // 回転保存
  });

  it("401→refresh失敗→clear+onSessionExpired+ApiError(Review Focus 2)", async () => {
    const session = makeSession();
    session.save({ access_token: "expired", refresh_token: "rt-dead" });
    const fetchImpl = vi
      .fn()
      .mockResolvedValueOnce(jsonResponse(401, { error: { code: "UNAUTHENTICATED", message: "x", details: null } }))
      .mockResolvedValueOnce(jsonResponse(401, { error: { code: "INVALID_REFRESH_TOKEN", message: "x", details: null } }));
    const onSessionExpired = vi.fn();
    const client = createClient({ session, fetchImpl, onSessionExpired });
    await expect(client.call("GET", "/v1/intents/expiry-options")).rejects.toMatchObject({
      status: 401,
      code: "UNAUTHENTICATED",
    });
    expect(onSessionExpired).toHaveBeenCalledTimes(1);
    expect(session.hasTokens()).toBe(false);
    expect(fetchImpl).toHaveBeenCalledTimes(2); // 再送しない
  });

  it("signalをfetchへ透過する(parseのabort用)", async () => {
    const session = makeSession();
    session.save({ access_token: "at-1", refresh_token: "rt-1" });
    const fetchImpl = vi.fn(async () => jsonResponse(200, { ok: 1 }));
    const client = createClient({ session, fetchImpl });
    const controller = new AbortController();
    await client.call("POST", "/v1/intents/parse", {
      body: { text: "…" },
      signal: controller.signal,
    });
    expect(fetchImpl.mock.calls[0][1].signal).toBe(controller.signal);
  });
});
