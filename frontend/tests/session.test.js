import { describe, expect, it, vi } from "vitest";
import { createSession, exchangeIdpToken } from "../src/api/session.js";

const memoryStorage = () => {
  const store = new Map();
  return {
    getItem: (k) => (store.has(k) ? store.get(k) : null),
    setItem: (k, v) => store.set(k, String(v)),
    removeItem: (k) => store.delete(k),
  };
};

const storages = () => ({ storage: { session: memoryStorage(), local: memoryStorage() } });
const ok = (body) => new Response(JSON.stringify(body), { status: 200 });

describe("createSession(トークン保存場所 — design §2.3)", () => {
  it("accessはsessionStorage+メモリ・refreshはlocalStorage", () => {
    const session = createSession(storages());
    session.save({ access_token: "at-1", refresh_token: "rt-1" });
    expect(session.current()).toEqual({ accessToken: "at-1", refreshToken: "rt-1" });
    expect(session.hasTokens()).toBe(true);
  });

  it("clearで両方消える", () => {
    const session = createSession(storages());
    session.save({ access_token: "at-1", refresh_token: "rt-1" });
    session.clear();
    expect(session.current()).toEqual({ accessToken: null, refreshToken: null });
    expect(session.hasTokens()).toBe(false);
  });

  it("refreshのみ保持でもhasTokens(再訪継続利用)", () => {
    const session = createSession(storages());
    session.save({ access_token: null, refresh_token: "rt-1" });
    expect(session.hasTokens()).toBe(true);
  });
});

describe("exchangeIdpToken(本番フローと共通の交換経路)", () => {
  it("200ならトークン2値を返す(リクエスト形式を検証)", async () => {
    const fetchImpl = vi.fn(async () => ok({ access_token: "at-1", refresh_token: "rt-1" }));
    const tokens = await exchangeIdpToken(fetchImpl, { provider: "google", idpToken: "idp-1" });
    expect(tokens).toEqual({ access_token: "at-1", refresh_token: "rt-1" });
    const [url, init] = fetchImpl.mock.calls[0];
    expect(url).toBe("/v1/auth/token");
    expect(JSON.parse(init.body)).toEqual({ provider: "google", idp_token: "idp-1" });
  });

  it("401 INVALID_IDP_TOKENはApiError(呼び出し側でパネルに表示)", async () => {
    const fetchImpl = vi.fn(async () =>
      new Response(
        JSON.stringify({ error: { code: "INVALID_IDP_TOKEN", message: "x", details: null } }),
        { status: 401 },
      ),
    );
    await expect(
      exchangeIdpToken(fetchImpl, { provider: "google", idpToken: "bad" }),
    ).rejects.toMatchObject({ status: 401, code: "INVALID_IDP_TOKEN" });
  });
});
