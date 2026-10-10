import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { initConfirmScreen } from "../src/intent/confirm.js";
import { applyParseResult, createFormState } from "../src/intent/state.js";

// now実行時のJST暦に依存しない値(翌日以降の日付を使う)
const PARSED = {
  category: { primary: "drinking", secondary: null },
  alcohol_involved: true,
  time: { start: "2026-12-01T20:00:00+09:00", end: null },
  location: { name: "天文館", radius_m: null },
  budget: { max: 5000, currency: "JPY" },
  participants: { min: 2, max: 4 },
  soft_constraints: [],
  negative_constraints: [],
  ng_unverifiable: [],
};

const EXPIRY_DATA = {
  options: [
    { label: "今日 22:00", expires_at: "2026-12-01T22:00:00+09:00", selectable: true },
    { label: "今日 23:59", expires_at: "2026-12-01T23:59:00+09:00", selectable: true },
    { label: "明日 5:00", expires_at: "2026-12-02T05:00:00+09:00", selectable: true },
  ],
  default_index: 1,
};

// index.htmlの#confirmScreen骨格(動的部位のみ)
const SKELETON = `
<section id="confirmScreen" hidden>
  <p class="words-text" id="wordsText"></p>
  <div id="confirmRows"></div>
  <div class="extra-chips" id="extraChips"></div>
  <select id="expiry" class="field" disabled></select>
  <span id="expiryLabel">—</span>
  <span id="slotSummary"></span><span id="slotSummaryDot"></span>
  <button type="button" class="cta deposit-button" id="depositButton"><span class="cta-label">この条件で預ける</span></button>
  <p id="depositHelp"></p>
  <p id="confirmError" role="alert" hidden></p>
</section>`;

const mount = (callImpl, state) => {
  document.body.innerHTML = SKELETON;
  const client = { call: vi.fn(callImpl) };
  const chrome = { showToast: vi.fn() };
  const screen = initConfirmScreen({ client, chrome, state });
  return { client, chrome, screen };
};

const parsedState = () =>
  applyParseResult(
    { ...createFormState(), rawText: "12月1日20時以降、天文館で2〜4人、予算5,000円までなら軽く飲みたい" },
    PARSED,
  );

beforeEach(() => {
  window.location.hash = "";
});

afterEach(() => {
  window.location.hash = "";
});

describe("02読み解き確認(UI v2 Phase 2)", () => {
  it("enter: あなたの言葉・5行・足せる条件3値を描画しCTAは人数確認まで無効", async () => {
    const { screen } = mount(async () => EXPIRY_DATA, parsedState());
    screen.enter();
    const root = document.querySelector("#confirmScreen");
    // マーク=行番号との対応(D-03。vhの読み上げ補助はtextContentに混ざるため
    // mark要素単位で確認する)
    const marks = [...root.querySelectorAll("#wordsText mark")].map((m) => m.textContent);
    expect(marks).toEqual([
      "20時以降（条件1）",
      "天文館（条件2）",
      "2〜4人（条件3）",
      "予算5,000円まで（条件4）",
    ]);
    expect(root.querySelectorAll(".slot-row").length).toBe(5);
    expect(root.querySelectorAll(".extra-chip").length).toBe(3);
    expect(root.querySelector("#slotSummary").textContent).toBe("5項目 · 確認が必要なもの 1件");
    expect(root.querySelector("#depositButton").disabled).toBe(true);
    expect(root.querySelector("#depositHelp").textContent).toBe(
      "「03 人数」の確認に答えると、預けられます。",
    );
  });

  it("enter: stateが空(01を経由しない直接訪問)なら01へ戻す", () => {
    const { screen } = mount(async () => EXPIRY_DATA, createFormState());
    screen.enter();
    expect(window.location.hash).toBe("#/");
  });

  it("人数確認「含む」でCTAが有効になり、保存はPOST /v1/intents(status=active・既定値)", async () => {
    const state = parsedState();
    const { client, screen } = mount(async (method, path) => {
      if (path.startsWith("/v1/intents/expiry-options")) return EXPIRY_DATA;
      return { intent: { id: "i-1", status: "active" } };
    }, state);
    screen.enter();
    await vi.waitFor(() =>
      expect(document.querySelector("#expiry").disabled).toBe(false),
    );

    document
      .querySelector('#confirmRows input[data-action="pick-includes-owner"][value="in"]')
      .click();
    expect(document.querySelector("#depositButton").disabled).toBe(false);
    expect(document.querySelector("#slotSummary").textContent).toBe("5項目 · すべて確認済み");

    document.querySelector("#depositButton").click();
    await vi.waitFor(() =>
      expect(client.call).toHaveBeenCalledWith("POST", "/v1/intents", expect.anything()),
    );
    const body = client.call.mock.calls.at(-1)[2].body;
    expect(body.status).toBe("active");
    expect(body.structured_intent.visibility).toBe("hidden_until_match");
    expect(body.structured_intent.notification_level).toBe("proposals_only");
    expect(body.structured_intent.participants).toEqual({ min: 2, max: 4 });
    expect(body.structured_intent.participants_includes_owner).toBe(true);
    // 期限selectの既定(23:59)をexpires_atとして送る(D-01)
    expect(body.structured_intent.expires_at).toBe("2026-12-01T23:59:00+09:00");
    // 保存に成功したら03(プレースホルダ)へ遷移
    await vi.waitFor(() => expect(window.location.hash).toBe("#/standby"));
  });

  it("人数確認「含まない」は+1換算で送る(D-04)", async () => {
    const state = parsedState();
    const { client, screen } = mount(async (method, path) => {
      if (path.startsWith("/v1/intents/expiry-options")) return EXPIRY_DATA;
      return { intent: { id: "i-2", status: "active" } };
    }, state);
    screen.enter();
    await vi.waitFor(() =>
      expect(document.querySelector("#expiry").disabled).toBe(false),
    );
    document
      .querySelector('#confirmRows input[data-action="pick-includes-owner"][value="out"]')
      .click();
    document.querySelector("#depositButton").click();
    await vi.waitFor(() =>
      expect(client.call).toHaveBeenCalledWith("POST", "/v1/intents", expect.anything()),
    );
    const body = client.call.mock.calls.at(-1)[2].body;
    expect(body.structured_intent.participants).toEqual({ min: 3, max: 5 });
    expect(body.structured_intent.participants_includes_owner).toBe(false);
  });

  it("足せる条件のトグルはsoft_constraintsへ追記される", async () => {
    const state = parsedState();
    const { client, screen } = mount(async (method, path) => {
      if (path.startsWith("/v1/intents/expiry-options")) return EXPIRY_DATA;
      return { intent: { id: "i-3", status: "active" } };
    }, state);
    screen.enter();
    document
      .querySelector('#confirmRows input[data-action="pick-includes-owner"][value="in"]')
      .click();
    document.querySelector('[data-action="toggle-extra"][data-label="禁煙の店"]').click();
    document.querySelector("#depositButton").click();
    await vi.waitFor(() =>
      expect(client.call).toHaveBeenCalledWith("POST", "/v1/intents", expect.anything()),
    );
    const body = client.call.mock.calls.at(-1)[2].body;
    expect(body.structured_intent.soft_constraints).toEqual(["禁煙の店"]);
  });

  it("期限selectはAPI応答で構成されラベルに反映される(D-01・3択)", async () => {
    const { screen } = mount(async () => EXPIRY_DATA, parsedState());
    screen.enter();
    await vi.waitFor(() =>
      expect(document.querySelector("#expiry").disabled).toBe(false),
    );
    const select = document.querySelector("#expiry");
    expect([...select.options].map((o) => o.textContent)).toEqual([
      "今日 22:00",
      "今日 23:59",
      "明日 5:00",
    ]);
    expect(document.querySelector("#expiryLabel").textContent).toBe("今日 23:59");
    select.selectedIndex = 2;
    select.dispatchEvent(new Event("change", { bubbles: true }));
    expect(document.querySelector("#expiryLabel").textContent).toBe("明日 5:00");
  });

  it("保存エラー(GEOCODING_FAILED)は場所行を強調しエラー文言を表示", async () => {
    const state = parsedState();
    const apiError = Object.assign(new Error("GEOCODING_FAILED"), {
      status: 422,
      code: "GEOCODING_FAILED",
    });
    const { screen } = mount(async (method, path) => {
      if (path.startsWith("/v1/intents/expiry-options")) return EXPIRY_DATA;
      throw apiError;
    }, state);
    screen.enter();
    await vi.waitFor(() =>
      expect(document.querySelector("#expiry").disabled).toBe(false),
    );
    document
      .querySelector('#confirmRows input[data-action="pick-includes-owner"][value="in"]')
      .click();
    document.querySelector("#depositButton").click();
    await vi.waitFor(() =>
      expect(document.querySelector("#confirmError").hidden).toBe(false),
    );
    expect(
      document.querySelector('.slot-row[data-key="location"]').classList.contains("slot-error"),
    ).toBe(true);
    expect(window.location.hash).toBe(""); // 遷移しない
  });

  it("あなたの言葉のマークは原文由来で組み立てられる(D-03・textContentのみ)", () => {
    const state = parsedState();
    state.rawText = '<img src=x onerror=alert(1)>今日20時以降';
    const { screen } = mount(async () => EXPIRY_DATA, state);
    screen.enter();
    const words = document.querySelector("#wordsText");
    expect(words.querySelector("mark")).not.toBeNull();
    expect(words.innerHTML).not.toContain("<img");
  });
});
