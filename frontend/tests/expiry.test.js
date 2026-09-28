import { beforeEach, describe, expect, it } from "vitest";
import { applyExpiryOptions, fetchExpiryOptions, selectedExpiry } from "../src/intent/expiry.js";

beforeEach(() => {
  document.body.innerHTML = '<select id="expiry"></select>';
});

const select = () => document.querySelector("#expiry");

const DATA = {
  options: [
    { label: "今夜 23:30", expires_at: "2026-09-27T23:30:00+09:00", selectable: true },
    { label: "明日 12:00", expires_at: "2026-09-28T12:00:00+09:00", selectable: true },
    { label: "明日 23:30", expires_at: "2026-09-28T23:30:00+09:00", selectable: true },
    { label: "3日後まで", expires_at: "2026-09-30T21:00:00+09:00", selectable: true },
  ],
  default_index: 1,
};

describe("fetchExpiryOptions(design §2.6)", () => {
  it("time_startなしでGETする", async () => {
    const calls = [];
    const client = { call: async (method, path) => (calls.push([method, path]), DATA) };
    await fetchExpiryOptions(client, null);
    expect(calls).toEqual([["GET", "/v1/intents/expiry-options"]]);
  });

  it("time_startはURLエンコードしてクエリへ", async () => {
    const calls = [];
    const client = { call: async (method, path) => (calls.push(path), DATA) };
    await fetchExpiryOptions(client, "2026-09-27T20:00:00+09:00");
    expect(calls[0]).toBe(
      `/v1/intents/expiry-options?time_start=${encodeURIComponent("2026-09-27T20:00:00+09:00")}`,
    );
  });
});

describe("applyExpiryOptions", () => {
  it("label表示・value=絶対時刻・default_indexを選択", () => {
    const idx = applyExpiryOptions(select(), DATA);
    expect(idx).toBe(1);
    const options = [...select().options];
    expect(options.map((o) => o.textContent)).toEqual([
      "今夜 23:30",
      "明日 12:00",
      "明日 23:30",
      "3日後まで",
    ]);
    expect(options[0].value).toBe("2026-09-27T23:30:00+09:00");
    expect(select().selectedIndex).toBe(1);
  });

  it("selectable=falseの選択肢はdisabled(過ぎた選択肢・確定値6)", () => {
    const passed = {
      options: DATA.options.map((o, i) => (i === 0 ? { ...o, selectable: false } : o)),
      default_index: 1,
    };
    applyExpiryOptions(select(), passed);
    expect(select().options[0].disabled).toBe(true);
    expect(select().options[1].disabled).toBe(false);
  });

  it("default_index=null(時間未確定)は最初の選択可能肢を選ぶ(Review Focus 3)", () => {
    const noDefault = {
      options: DATA.options.map((o, i) => (i === 0 ? { ...o, selectable: false } : o)),
      default_index: null,
    };
    const idx = applyExpiryOptions(select(), noDefault);
    expect(idx).toBe(1);
    expect(select().value).toBe("2026-09-28T12:00:00+09:00");
  });
});

describe("selectedExpiry", () => {
  it("選択中のlabelと絶対時刻を返す(クライアントは絶対時刻をexpires_atとして送る)", () => {
    applyExpiryOptions(select(), DATA);
    expect(selectedExpiry(select())).toEqual({
      label: "明日 12:00",
      expiresAt: "2026-09-28T12:00:00+09:00",
    });
  });
});
