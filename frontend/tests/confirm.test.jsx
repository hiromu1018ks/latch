import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { act, cleanup, fireEvent, render, waitFor } from "@testing-library/react";
import { useState } from "react";
import { ConfirmScreen } from "../src/intent/ConfirmScreen.jsx";
import { applyParseResult, createFormState } from "../src/intent/state.js";
import { ChromeProvider } from "../src/ui/chrome.jsx";

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

// ConfirmScreenを1つのフォームstateで描く(App配線と同じ共有stateの形)。
// UI v2 Phase 6から02の組立はReactコンポーネントのため、旧initConfirmScreenの
// 試験意図=描画・遷移・送信ボディ・エラー表示をRTLで踏襲する。
const mountConfirm = (callImpl, state, intentId = null) => {
  cleanup();
  const client = { call: vi.fn(callImpl) };
  const stateRef = { current: state };
  const holder = {};
  const Harness = () => {
    const [s, setS] = useState(stateRef.current);
    const update = (updater) => {
      const next = typeof updater === "function" ? updater(stateRef.current) : updater;
      stateRef.current = next;
      setS(next);
    };
    holder.update = update;
    return (
      <ChromeProvider>
        <ConfirmScreen
          client={client}
          state={s}
          getState={() => stateRef.current}
          setState={update}
          intentId={intentId}
        />
      </ChromeProvider>
    );
  };
  const utils = render(<Harness />);
  return { client, utils, holder };
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
  cleanup();
  window.location.hash = "";
});

describe("02読み解き確認(UI v2 Phase 2)", () => {
  it("enter: あなたの言葉・5行・足せる条件3値を描画しCTAは人数確認まで無効", async () => {
    mountConfirm(async () => EXPIRY_DATA, parsedState());
    await waitFor(() => {
      expect(document.querySelectorAll(".slot-row").length).toBe(5);
    });
    // マーク=行番号との対応(D-03。vhの読み上げ補助はtextContentに混ざるため
    // mark要素単位で確認する)
    const marks = [...document.querySelectorAll("#wordsText mark")].map((m) => m.textContent);
    expect(marks).toEqual([
      "20時以降（条件1）",
      "天文館（条件2）",
      "2〜4人（条件3）",
      "予算5,000円まで（条件4）",
    ]);
    expect(document.querySelectorAll(".extra-chip").length).toBe(3);
    expect(document.querySelector("#slotSummary").textContent).toBe("5項目 · 確認が必要なもの 1件");
    expect(document.querySelector("#depositButton").disabled).toBe(true);
    expect(document.querySelector("#depositHelp").textContent).toBe(
      "「03 人数」の確認に答えると、預けられます。",
    );
  });

  it("enter: stateが空(01を経由しない直接訪問)なら01へ戻す", async () => {
    mountConfirm(async () => EXPIRY_DATA, createFormState());
    await waitFor(() => expect(window.location.hash).toBe("#/"));
  });

  it("人数確認「含む」でCTAが有効になり、保存はPOST /v1/intents(status=active・既定値)", async () => {
    const state = parsedState();
    const { client } = mountConfirm(async (method, path) => {
      if (path.startsWith("/v1/intents/expiry-options")) return EXPIRY_DATA;
      return { intent: { id: "i-1", status: "active" } };
    }, state);
    await waitFor(() =>
      expect(document.querySelector("#expiry").disabled).toBe(false),
    );

    fireEvent.click(
      document.querySelector('#confirmRows input[data-action="pick-includes-owner"][value="in"]'),
    );
    await act(async () => {});
    expect(document.querySelector("#depositButton").disabled).toBe(false);
    expect(document.querySelector("#slotSummary").textContent).toBe("5項目 · すべて確認済み");

    fireEvent.click(document.querySelector("#depositButton"));
    await waitFor(() =>
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
    // 保存に成功したら03の着地状態へ遷移
    await waitFor(() => expect(window.location.hash).toBe("#/standby?deposited=1"));
  });

  it("人数確認「含まない」は+1換算で送る(D-04)", async () => {
    const state = parsedState();
    const { client } = mountConfirm(async (method, path) => {
      if (path.startsWith("/v1/intents/expiry-options")) return EXPIRY_DATA;
      return { intent: { id: "i-2", status: "active" } };
    }, state);
    await waitFor(() =>
      expect(document.querySelector("#expiry").disabled).toBe(false),
    );
    fireEvent.click(
      document.querySelector('#confirmRows input[data-action="pick-includes-owner"][value="out"]'),
    );
    await act(async () => {});
    fireEvent.click(document.querySelector("#depositButton"));
    await waitFor(() =>
      expect(client.call).toHaveBeenCalledWith("POST", "/v1/intents", expect.anything()),
    );
    const body = client.call.mock.calls.at(-1)[2].body;
    expect(body.structured_intent.participants).toEqual({ min: 3, max: 5 });
    expect(body.structured_intent.participants_includes_owner).toBe(false);
  });

  it("足せる条件のトグルはsoft_constraintsへ追記される", async () => {
    const state = parsedState();
    const { client } = mountConfirm(async (method, path) => {
      if (path.startsWith("/v1/intents/expiry-options")) return EXPIRY_DATA;
      return { intent: { id: "i-3", status: "active" } };
    }, state);
    await waitFor(() =>
      expect(document.querySelector("#expiry").disabled).toBe(false),
    );
    fireEvent.click(
      document.querySelector('#confirmRows input[data-action="pick-includes-owner"][value="in"]'),
    );
    await act(async () => {});
    fireEvent.click(document.querySelector('[data-action="toggle-extra"][data-label="禁煙の店"]'));
    await act(async () => {});
    fireEvent.click(document.querySelector("#depositButton"));
    await waitFor(() =>
      expect(client.call).toHaveBeenCalledWith("POST", "/v1/intents", expect.anything()),
    );
    const body = client.call.mock.calls.at(-1)[2].body;
    expect(body.structured_intent.soft_constraints).toEqual(["禁煙の店"]);
  });

  it("期限selectはAPI応答で構成されラベルに反映される(D-01・3択)", async () => {
    mountConfirm(async () => EXPIRY_DATA, parsedState());
    await waitFor(() =>
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
    fireEvent.change(select);
    await act(async () => {});
    expect(document.querySelector("#expiryLabel").textContent).toBe("明日 5:00");
  });

  it("保存エラー(GEOCODING_FAILED)は場所行を強調しエラー文言を表示", async () => {
    const state = parsedState();
    const apiError = Object.assign(new Error("GEOCODING_FAILED"), {
      status: 422,
      code: "GEOCODING_FAILED",
    });
    mountConfirm(async (method, path) => {
      if (path.startsWith("/v1/intents/expiry-options")) return EXPIRY_DATA;
      throw apiError;
    }, state);
    await waitFor(() =>
      expect(document.querySelector("#expiry").disabled).toBe(false),
    );
    fireEvent.click(
      document.querySelector('#confirmRows input[data-action="pick-includes-owner"][value="in"]'),
    );
    await act(async () => {});
    fireEvent.click(document.querySelector("#depositButton"));
    await waitFor(() =>
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
    mountConfirm(async () => EXPIRY_DATA, state);
    const words = document.querySelector("#wordsText");
    expect(words.querySelector("mark")).not.toBeNull();
    expect(words.innerHTML).not.toContain("<img");
  });

  it("編集モード(intentIdあり): 保存済みIntentを復元してPATCH保存する(03の「条件を直す」)", async () => {
    const state = parsedState();
    const intent = {
      id: "i-edit",
      status: "active",
      raw_text: "12月1日20時以降、天文館で2〜4人、予算5,000円までなら軽く飲みたい",
      structured_intent: {
        category: PARSED.category,
        alcohol_involved: true,
        time: PARSED.time,
        location: PARSED.location,
        budget: PARSED.budget,
        participants: PARSED.participants,
        participants_includes_owner: true,
        soft_constraints: [],
        ng_unverifiable: [],
      },
      expires_at: "2026-12-01T23:59:00+09:00",
    };
    const { client } = mountConfirm(async (method, path) => {
      if (path === `/v1/intents/${intent.id}`) return { intent };
      if (path.startsWith("/v1/intents/expiry-options")) return EXPIRY_DATA;
      return { intent };
    }, state, intent.id);
    await waitFor(() =>
      expect(document.querySelector("#expiry").disabled).toBe(false),
    );
    // 復元: segは「確認済み」(participants_includes_owner=true)・CTA有効
    expect(document.querySelector(".people-box.ok .people-tag").textContent).toBe("確認済み");
    expect(document.querySelector("#depositButton").disabled).toBe(false);
    // 保存はPATCH /v1/intents/{id}(全置換・status省略=現状維持)
    fireEvent.click(document.querySelector("#depositButton"));
    await waitFor(() =>
      expect(client.call).toHaveBeenCalledWith("PATCH", `/v1/intents/${intent.id}`, expect.anything()),
    );
    const body = client.call.mock.calls.at(-1)[2].body;
    expect(body.status).toBeUndefined();
    expect(body.structured_intent.participants_includes_owner).toBe(true);
    // 編集モードの保存後は通常の待機中へ
    await waitFor(() => expect(window.location.hash).toBe("#/standby"));
  });

  it("下書き編集モード: CTA保存にstatus=activeを添える(D-13)", async () => {
    const state = parsedState();
    const intent = {
      id: "i-draft",
      status: "draft",
      raw_text: "12月1日20時以降、天文館で2〜4人、予算5,000円までなら軽く飲みたい",
      structured_intent: {
        category: PARSED.category,
        alcohol_involved: true,
        time: PARSED.time,
        location: PARSED.location,
        budget: PARSED.budget,
        participants: PARSED.participants,
        participants_includes_owner: true,
        soft_constraints: [],
        ng_unverifiable: [],
      },
      expires_at: null,
    };
    const { client } = mountConfirm(async (method, path) => {
      if (path === `/v1/intents/${intent.id}`) return { intent };
      if (path.startsWith("/v1/intents/expiry-options")) return EXPIRY_DATA;
      return { intent };
    }, state, intent.id);
    await waitFor(() =>
      expect(document.querySelector("#depositButton").disabled).toBe(false),
    );
    fireEvent.click(document.querySelector("#depositButton"));
    await waitFor(() =>
      expect(client.call).toHaveBeenCalledWith("PATCH", `/v1/intents/${intent.id}`, expect.anything()),
    );
    const body = client.call.mock.calls.at(-1)[2].body;
    expect(body.status).toBe("active"); // draft→active化(D-13)
  });

  it("編集モード: 復元失敗(消済み・他者)は待機中へ戻す", async () => {
    mountConfirm(async () => {
      throw Object.assign(new Error("x"), { status: 404, code: "NOT_FOUND" });
    }, parsedState(), "i-gone");
    await waitFor(() => expect(window.location.hash).toBe("#/standby"));
  });
});
