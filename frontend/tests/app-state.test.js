import { describe, expect, it, vi } from "vitest";
import { createAppState } from "../src/appState.js";

// GET /v1/users/me の応答(§2-1: フラット・envelopeなし)
const meResponse = {
  id: "22222222-2222-4222-8222-222222222222",
  display_name: "たろう",
  profile: { bio: "よろしく" },
  birth_date: "1990-04-01",
  profile_complete: true,
};

describe("createAppState(design §2.6)", () => {
  it("ensureMeはGET /v1/users/meを1回だけ呼びメモリ保持する", async () => {
    const client = { call: vi.fn(async () => meResponse) };
    const state = createAppState({ client });
    const first = await state.ensureMe();
    const second = await state.ensureMe();
    expect(client.call).toHaveBeenCalledTimes(1);
    expect(client.call).toHaveBeenCalledWith("GET", "/v1/users/me");
    expect(first).toEqual({
      id: meResponse.id,
      display_name: "たろう",
      profile: { bio: "よろしく" },
    });
    expect(second).toBe(first); // 同一オブジェクト(キャッシュ)
    expect(state.me.id).toBe(meResponse.id);
  });

  it("初期状態のmeはnull", () => {
    const state = createAppState({ client: { call: vi.fn() } });
    expect(state.me).toBeNull();
  });

  it("resetすると次回で再取得する(再ログイン)", async () => {
    const client = { call: vi.fn(async () => meResponse) };
    const state = createAppState({ client });
    await state.ensureMe();
    state.reset();
    expect(state.me).toBeNull();
    await state.ensureMe();
    expect(client.call).toHaveBeenCalledTimes(2);
  });
});
