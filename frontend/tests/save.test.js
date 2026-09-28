import { describe, expect, it, vi } from "vitest";
import { createSaveFlow, errorPlacement } from "../src/intent/save.js";
import { applyParseFallback, applyParseResult, createFormState } from "../src/intent/state.js";

const PARSED = {
  category: { primary: "drinking", secondary: null },
  alcohol_involved: true,
  time: { start: "2026-09-27T20:00:00+09:00", end: null },
  location: { name: "天文館", radius_m: null },
  budget: { max: 5000, currency: "JPY" },
  participants: { min: 2, max: 4 },
  soft_constraints: ["軽く飲みたい"],
  negative_constraints: [],
  ng_unverifiable: ["会社関係の人は避けたい"],
};

const apiError = (status, code, message) =>
  Object.assign(new Error(message ?? code), { name: "ApiError", status, code });

const makeFlow = (state, callImpl) => {
  const flow = {};
  flow.client = { call: vi.fn(callImpl) };
  flow.getOptions = () => ({
    visibility: "hidden_until_match",
    notificationLevel: "proposals_only",
    expiresAt: "2026-09-27T23:30:00+09:00",
  });
  flow.handlers = {
    onBusy: vi.fn(),
    onActiveSaved: vi.fn(),
    onDraftSaved: vi.fn(),
    onFormError: vi.fn(),
  };
  flow.save = createSaveFlow({
    client: flow.client,
    getState: () => state,
    getOptions: flow.getOptions,
    ...flow.handlers,
  });
  return flow;
};

describe("errorPlacement(design §2.9の表示マップ)", () => {
  it("GEOCODING_FAILED → 場所行", () => {
    expect(errorPlacement(apiError(422, "GEOCODING_FAILED"))).toMatchObject({
      target: "location",
    });
  });

  it("VALIDATION_ERRORのtime.start/expires_at → 時間行", () => {
    expect(
      errorPlacement(apiError(422, "VALIDATION_ERROR", "time.start is in the past")),
    ).toMatchObject({ target: "time" });
    expect(
      errorPlacement(apiError(422, "VALIDATION_ERROR", "expires_at exceeds 7 days")),
    ).toMatchObject({ target: "time" });
  });

  it("未知のVALIDATION_ERROR message → グローバル(Review Focus 6)", () => {
    expect(
      errorPlacement(apiError(422, "VALIDATION_ERROR", "category is required")),
    ).toMatchObject({ target: "global" });
    expect(errorPlacement(apiError(422, "VALIDATION_ERROR", null))).toMatchObject({
      target: "global",
    });
  });

  it("UNDER_AGE / ACTIVE_INTENT_LIMIT / RATE_LIMITED / 503系 → グローバル", () => {
    for (const code of [
      "UNDER_AGE",
      "ACTIVE_INTENT_LIMIT",
      "RATE_LIMITED",
      "LLM_UNAVAILABLE",
      "DEPENDENCY_UNAVAILABLE",
    ]) {
      expect(errorPlacement(apiError(code === "RATE_LIMITED" ? 429 : 422, code))).toMatchObject({
        target: "global",
      });
    }
  });
});

describe("createSaveFlow", () => {
  it("saveActive: 必須3充足ならPOST /v1/intents(status=active)し201でonActiveSaved", async () => {
    const state = { ...applyParseResult(createFormState(), PARSED), rawText: "今日20時以降、天文館で軽く飲みたい。" };
    const calls = [];
    const flow = makeFlow(state, async (method, path, opts) => {
      calls.push([method, path, opts?.body]);
      return { intent: { id: "i-1", status: "active" } };
    });
    await flow.save.saveActive();
    expect(calls[0][0]).toBe("POST");
    expect(calls[0][1]).toBe("/v1/intents");
    expect(calls[0][2].status).toBe("active");
    expect(calls[0][2].structured_intent.expires_at).toBe("2026-09-27T23:30:00+09:00");
    expect(flow.handlers.onActiveSaved).toHaveBeenCalledTimes(1);
    expect(flow.handlers.onFormError).not.toHaveBeenCalled();
  });

  it("saveActive: 必須3欠落なら送信しない(フロント事前ブロック・確定値4)", async () => {
    const state = { ...createFormState(), rawText: "今日20時以降、天文館で軽く飲みたい。" };
    const flow = makeFlow(state, async () => ({}));
    await flow.save.saveActive();
    expect(flow.client.call).not.toHaveBeenCalled();
  });

  it("saveDraft: rawTextのみで保存できる(fallback状態でも・Review Focus 1)", async () => {
    const parsed = { ...applyParseResult(createFormState(), PARSED), rawText: "今日20時以降、天文館で軽く飲みたい。" };
    const state = applyParseFallback(parsed); // 必須3が揃わない状態
    const calls = [];
    const flow = makeFlow(state, async (method, path, opts) => {
      calls.push(opts?.body);
      return { intent: { id: "i-2", status: "draft" } };
    });
    await flow.save.saveDraft();
    expect(calls[0].status).toBe("draft");
    expect(flow.handlers.onDraftSaved).toHaveBeenCalledTimes(1);
  });

  it("saveDraft: テキスト空なら送信しない", async () => {
    const flow = makeFlow(createFormState(), async () => ({}));
    await flow.save.saveDraft();
    expect(flow.client.call).not.toHaveBeenCalled();
  });

  it("エラー時はonFormError(placement)へ", async () => {
    const state = { ...applyParseResult(createFormState(), PARSED), rawText: "今日20時以降、天文館で軽く飲みたい。" };
    const flow = makeFlow(state, async () => {
      throw apiError(422, "GEOCODING_FAILED");
    });
    await flow.save.saveActive();
    expect(flow.handlers.onFormError).toHaveBeenCalledWith(
      expect.objectContaining({ target: "location" }),
    );
    expect(flow.handlers.onActiveSaved).not.toHaveBeenCalled();
  });

  it("保存中は二重送信しない(onBusy・Review Focus 8)", async () => {
    const state = { ...applyParseResult(createFormState(), PARSED), rawText: "今日20時以降、天文館で軽く飲みたい。" };
    let release;
    const gate = new Promise((res) => (release = res));
    const busyStates = [];
    const flow = makeFlow(state, async () => {
      busyStates.push(flow.handlers.onBusy.mock.calls.at(-1)?.[0] ?? null);
      await gate;
      return { intent: {} };
    });
    const first = flow.save.saveActive();
    await Promise.resolve();
    await flow.save.saveActive(); // in-flight中の再押下
    release();
    await first;
    expect(flow.client.call).toHaveBeenCalledTimes(1);
    expect(busyStates[0]).toBe(true);
    expect(flow.handlers.onBusy).toHaveBeenLastCalledWith(false);
  });
});
