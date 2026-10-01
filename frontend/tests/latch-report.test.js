import { describe, expect, it, vi } from "vitest";
import { REPORT_TOAST_TEXT } from "../src/latch/texts.js";
import { createReportFlow } from "../src/latch/report.js";

const LATCH_ID = "11111111-1111-4111-8111-111111111111";
const ME = "22222222-2222-4222-8222-222222222222";
const PEER = "33333333-3333-4333-8333-333333333333";
const THIRD = "44444444-4444-4444-8444-444444444444";

const makeFlow = (callImpl) => {
  const client = { call: vi.fn(callImpl ?? (async () => ({ report_id: "r1" }))) };
  const chrome = { showToast: vi.fn() };
  const modal = document.createElement("div");
  const flow = createReportFlow({ client, chrome, modal });
  return { client, chrome, modal, flow };
};

const selectReason = (modal, value) => {
  // happy-domはラジオの排他(uncheck)を実装しないため全解除→選択
  for (const radio of modal.querySelectorAll('input[name="reason"]')) {
    radio.checked = radio.value === value;
  }
};

describe("通報flow(design §2.7)", () => {
  it("成立済み1対1: 相手固定でreportee_id明示のbodyを送る", async () => {
    const { client, modal, flow } = makeFlow();
    flow.open({
      latchId: LATCH_ID,
      participants: [
        { user_id: ME, display_name: "自分", profile: {} },
        { user_id: PEER, display_name: "相手", profile: {} },
      ],
      meId: ME,
    });
    expect(modal.hidden).toBe(false);
    selectReason(modal, "inappropriate_content");
    await flow.submit();
    expect(client.call).toHaveBeenCalledWith("POST", "/v1/reports", {
      body: {
        reportee_id: PEER,
        latch_id: LATCH_ID,
        reason: "inappropriate_content",
      },
    });
  });

  it("成立済みグループ: 対象者を選択してreportee_id=選択値", async () => {
    const { client, modal, flow } = makeFlow();
    flow.open({
      latchId: LATCH_ID,
      participants: [
        { user_id: ME, display_name: "自分", profile: {} },
        { user_id: PEER, display_name: "相手A", profile: {} },
        { user_id: THIRD, display_name: "相手B", profile: {} },
      ],
      meId: ME,
    });
    modal.querySelector(`input[name="reportee"][value="${THIRD}"]`).checked = true;
    selectReason(modal, "other");
    await flow.submit();
    expect(client.call).toHaveBeenCalledWith("POST", "/v1/reports", {
      body: { reportee_id: THIRD, latch_id: LATCH_ID, reason: "other" },
    });
  });

  it("提案1対1(participants=null): reportee_idを省略したbody{latch_id, reason}(案X)", async () => {
    const { client, modal, flow } = makeFlow();
    flow.open({ latchId: LATCH_ID, participants: null, meId: ME });
    selectReason(modal, "unpleasant_behavior");
    await flow.submit();
    expect(client.call).toHaveBeenCalledWith("POST", "/v1/reports", {
      body: { latch_id: LATCH_ID, reason: "unpleasant_behavior" },
    });
  });

  it("理由4値のコードがそのままAPI値になる", async () => {
    const { client, modal, flow } = makeFlow();
    flow.open({ latchId: LATCH_ID, participants: null, meId: ME });
    for (const code of [
      "inappropriate_content",
      "unpleasant_behavior",
      "suspected_impersonation",
      "other",
    ]) {
      selectReason(modal, code);
      await flow.submit();
      expect(client.call.mock.calls.at(-1)[2].body.reason).toBe(code);
    }
  });

  it("201でモーダルを閉じトースト表示", async () => {
    const { chrome, modal, flow } = makeFlow();
    flow.open({ latchId: LATCH_ID, participants: null, meId: ME });
    selectReason(modal, "other");
    await flow.submit();
    expect(modal.hidden).toBe(true);
    expect(chrome.showToast).toHaveBeenCalledWith(REPORT_TOAST_TEXT);
  });

  it("理由未選択は送信せず案内文言(422を出さない)", async () => {
    const { client, modal, flow } = makeFlow();
    flow.open({ latchId: LATCH_ID, participants: null, meId: ME });
    await flow.submit(); // 何も選んでいない
    expect(client.call).not.toHaveBeenCalled();
    expect(modal.hidden).toBe(false); // 閉じない
    expect(modal.querySelector("[data-role=report-error]").hidden).toBe(false);
  });

  it("失敗時はモーダルを閉じずエラー表示(RATE_LIMITEDは既存文言)", async () => {
    const apiError = Object.assign(new Error("x"), {
      name: "ApiError", status: 429, code: "RATE_LIMITED",
    });
    const { client, modal, flow } = makeFlow(() => {
      throw apiError;
    });
    flow.open({ latchId: LATCH_ID, participants: null, meId: ME });
    selectReason(modal, "other");
    await flow.submit();
    expect(modal.hidden).toBe(false);
    expect(client.call).toHaveBeenCalledTimes(1);
  });
});
