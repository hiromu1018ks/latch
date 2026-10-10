import { describe, expect, it, vi } from "vitest";
import {
  RATE_LIMIT_TEXT,
  REPORT_REASON_OPTIONS,
  SAFETY_NOTE_TEXT,
} from "../src/latch/texts.js";
import { createSafetyFlow } from "../src/latch/safety.js";

const LATCH_ID = "11111111-1111-4111-8111-111111111111";
const ME = "22222222-2222-4222-8222-222222222222";
const PEER = "33333333-3333-4333-8333-333333333333";
const THIRD = "44444444-4444-4444-8444-444444444444";

const makeFlow = (callImpl) => {
  const client = { call: vi.fn(callImpl ?? (async () => ({ report_id: "r1" }))) };
  const onBlocked = vi.fn(async () => {});
  const dialog = document.createElement("dialog");
  const flow = createSafetyFlow({ client, dialog, onBlocked });
  return { client, onBlocked, dialog, flow };
};

const ME_PEER = [
  { user_id: ME, display_name: "自分", profile: {} },
  { user_id: PEER, display_name: "相手", profile: {} },
];

// happy-domはラジオの排他(uncheck)を実装しないため全解除→選択→change発火
const pick = (dialog, name, value) => {
  for (const radio of dialog.querySelectorAll(`input[name="${name}"]`)) {
    radio.checked = radio.value === value;
    if (radio.checked) radio.dispatchEvent(new Event("change"));
  }
};

describe("通報・ブロック統合dialog(D-09・プロトタイプ05)", () => {
  it("成立済み1対1: 対象は相手で選択済み・ブロックでPOST /v1/users/{id}/block", async () => {
    const { client, dialog, flow } = makeFlow(async () => ({ blocked_id: PEER }));
    flow.open({ latchId: LATCH_ID, participants: ME_PEER, meId: ME });
    expect(dialog.open).toBe(true);
    expect(dialog.querySelector("h2").textContent).toBe("通報・ブロック");
    expect(dialog.textContent).toContain("相手"); // 対象ラジオ(選択済み)
    expect(dialog.querySelector('input[name="safety-who"]').checked).toBe(true);
    expect(dialog.textContent).toContain(SAFETY_NOTE_TEXT);
    dialog.querySelector("[data-role=safety-block]").click();
    await vi.waitFor(() => {
      expect(client.call).toHaveBeenCalledWith("POST", `/v1/users/${PEER}/block`);
    });
    await vi.waitFor(() => {
      expect(dialog.querySelector("[role=status]").textContent)
        .toBe("相手さんをブロックしました。今後この相手とLATCHが成立することはありません。");
    });
  });

  it("ブロック成功でonBlocked(詳細再取得)が走る", async () => {
    const { dialog, flow, onBlocked } = makeFlow(async () => ({ blocked_id: PEER }));
    flow.open({ latchId: LATCH_ID, participants: ME_PEER, meId: ME });
    dialog.querySelector("[data-role=safety-block]").click();
    await vi.waitFor(() => expect(onBlocked).toHaveBeenCalledTimes(1));
  });

  it("グループ: 未選択では両ボタン無効・選択で有効化して選択値へPOST", async () => {
    const { client, dialog, flow } = makeFlow(async () => ({ blocked_id: THIRD }));
    flow.open({
      latchId: LATCH_ID,
      participants: [
        { user_id: ME, display_name: "自分", profile: {} },
        { user_id: PEER, display_name: "相手A", profile: {} },
        { user_id: THIRD, display_name: "相手B", profile: {} },
      ],
      meId: ME,
    });
    expect(dialog.querySelector("[data-role=safety-block]").disabled).toBe(true);
    expect(dialog.querySelector("[data-role=safety-report]").disabled).toBe(true);
    pick(dialog, "safety-who", THIRD);
    expect(dialog.querySelector("[data-role=safety-block]").disabled).toBe(false);
    dialog.querySelector("[data-role=safety-block]").click();
    await vi.waitFor(() => {
      expect(client.call).toHaveBeenCalledWith("POST", `/v1/users/${THIRD}/block`);
    });
  });

  it("通報は理由ステップ(D-09)を挿みreportee_id明示でPOST /v1/reports", async () => {
    const { client, dialog, flow } = makeFlow();
    flow.open({ latchId: LATCH_ID, participants: ME_PEER, meId: ME });
    dialog.querySelector("[data-role=safety-report]").click();
    // 理由4択のステップがdialog内に出る(対象ステップとは差し替わる)
    expect(dialog.querySelectorAll('input[name="safety-reason"]').length).toBe(4);
    expect(dialog.querySelector("[data-role=safety-report-submit]").disabled).toBe(true);
    pick(dialog, "safety-reason", "other");
    expect(dialog.querySelector("[data-role=safety-report-submit]").disabled).toBe(false);
    dialog.querySelector("[data-role=safety-report-submit]").click();
    await vi.waitFor(() => {
      expect(client.call).toHaveBeenCalledWith("POST", "/v1/reports", {
        body: { latch_id: LATCH_ID, reason: "other", reportee_id: PEER },
      });
    });
    await vi.waitFor(() => {
      expect(dialog.querySelector("[role=status]").textContent)
        .toBe("相手さんについての通報を受け付けました。運営が内容を確認します。");
    });
  });

  it("理由の4値コードがそのままAPI値になる", async () => {
    const { client, dialog, flow } = makeFlow();
    flow.open({ latchId: LATCH_ID, participants: null, meId: ME });
    for (const [code] of REPORT_REASON_OPTIONS) {
      dialog.querySelector("[data-role=safety-report]").click();
      pick(dialog, "safety-reason", code);
      dialog.querySelector("[data-role=safety-report-submit]").click();
      await vi.waitFor(() => {
        expect(client.call.mock.calls.at(-1)[2].body.reason).toBe(code);
      });
      // done状態から次の周回へ(doneの閉じる→開き直し)
      flow.open({ latchId: LATCH_ID, participants: null, meId: ME });
    }
  });

  it("提案1対1(participants=null): 対象ラジオなし・ブロックなし・reportee_id省略body(案X)", async () => {
    const { client, dialog, flow } = makeFlow();
    flow.open({ latchId: LATCH_ID, participants: null, meId: ME });
    expect(dialog.querySelector(".safety-targets")).toBeNull(); // 対象表示なし(非開示)
    expect(dialog.querySelector("[data-role=safety-block]")).toBeNull();
    expect(dialog.querySelector("[data-role=safety-report]").disabled).toBe(false);
    dialog.querySelector("[data-role=safety-report]").click();
    pick(dialog, "safety-reason", "unpleasant_behavior");
    dialog.querySelector("[data-role=safety-report-submit]").click();
    await vi.waitFor(() => {
      expect(client.call).toHaveBeenCalledWith("POST", "/v1/reports", {
        body: { latch_id: LATCH_ID, reason: "unpleasant_behavior" },
      });
    });
    await vi.waitFor(() => {
      expect(dialog.querySelector("[role=status]").textContent)
        .toBe("通報を受け付けました。運営が内容を確認します。");
    });
  });

  it("理由未選択の通報は送信せず案内文言", async () => {
    const { client, dialog, flow } = makeFlow();
    flow.open({ latchId: LATCH_ID, participants: null, meId: ME });
    dialog.querySelector("[data-role=safety-report]").click();
    dialog.querySelector("[data-role=safety-report-submit]").disabled = false; // 直接押下
    dialog.querySelector("[data-role=safety-report-submit]").click();
    expect(client.call).not.toHaveBeenCalled();
    expect(dialog.querySelector("[data-role=safety-error]").hidden).toBe(false);
  });

  it("理由ステップのもどるで対象ステップへ戻る", async () => {
    const { dialog, flow } = makeFlow();
    flow.open({ latchId: LATCH_ID, participants: ME_PEER, meId: ME });
    dialog.querySelector("[data-role=safety-report]").click();
    expect(dialog.querySelector("[data-role=safety-back]")).not.toBeNull();
    dialog.querySelector("[data-role=safety-back]").click();
    expect(dialog.querySelector("[data-role=safety-block]")).not.toBeNull();
  });

  it("失敗(429)はdialogを開いたままRATE_LIMIT_TEXT", async () => {
    const apiError = Object.assign(new Error("x"), {
      name: "ApiError", status: 429, code: "RATE_LIMITED",
    });
    const { client, dialog, flow } = makeFlow(() => {
      throw apiError;
    });
    flow.open({ latchId: LATCH_ID, participants: ME_PEER, meId: ME });
    dialog.querySelector("[data-role=safety-block]").click();
    await vi.waitFor(() => {
      expect(dialog.querySelector("[data-role=safety-error]").textContent)
        .toBe(RATE_LIMIT_TEXT);
    });
    expect(dialog.open).toBe(true);
  });

  it("done状態の閉じるでdialogを閉じる", async () => {
    const { dialog, flow } = makeFlow();
    flow.open({ latchId: LATCH_ID, participants: null, meId: ME });
    dialog.querySelector("[data-role=safety-report]").click();
    pick(dialog, "safety-reason", "other");
    dialog.querySelector("[data-role=safety-report-submit]").click();
    await vi.waitFor(() => {
      expect(dialog.querySelector("[data-role=safety-close]")).not.toBeNull();
    });
    dialog.querySelector("[data-role=safety-close]").click();
    expect(dialog.open).toBe(false);
  });
});
