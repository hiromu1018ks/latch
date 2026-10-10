import { describe, expect, it, vi } from "vitest";
import { ApiError } from "../src/api/session.js";
import { RATE_LIMIT_TEXT } from "../src/latch/texts.js";
import { createRespondFlow } from "../src/latch/respond.js";

const LATCH_ID = "11111111-1111-4111-8111-111111111111";

const apiError = (status, code) => new ApiError(status, code, "msg");

const makeFlow = (callImpl) => {
  const client = { call: vi.fn(callImpl) };
  const refresh = vi.fn(async () => {});
  const showError = vi.fn();
  const flow = createRespondFlow({ client, latchId: LATCH_ID, refresh, showError });
  const buttons = ["yes", "defer"].map((value) => {
    const button = document.createElement("button");
    button.dataset.value = value;
    return button;
  });
  return { client, refresh, showError, flow, buttons };
};

describe("回答flow(design §2.5・D-05〜D-07)", () => {
  it("2択のUI値→API値をbodyへ送る(yes/defer・D-05でno導線は廃止)", async () => {
    const { client, flow, buttons } = makeFlow(async () => ({ latch: {} }));
    await flow.submit("yes", { buttons });
    await flow.submit("defer", { buttons });
    expect(client.call.mock.calls[0]).toEqual([
      "POST",
      `/v1/latches/${LATCH_ID}/response`,
      { body: { response: "yes" } },
    ]);
    expect(client.call.mock.calls[1][2]).toEqual({ body: { response: "defer" } });
  });

  it("deferには選択した理由chipsを reasons としていっしょに送る(D-07)", async () => {
    const { client, flow, buttons } = makeFlow(async () => ({ latch: {} }));
    await flow.submit("defer", { buttons, reasons: ["time", "mood"] });
    expect(client.call.mock.calls[0][2]).toEqual({
      body: { response: "defer", reasons: ["time", "mood"] },
    });
  });

  it("yesにreasonsは付けない(defer時のみ受理のため)", async () => {
    const { client, flow, buttons } = makeFlow(async () => ({ latch: {} }));
    await flow.submit("yes", { buttons, reasons: ["time"] });
    expect(client.call.mock.calls[0][2]).toEqual({ body: { response: "yes" } });
  });

  it("undoはPOST /v1/latches/{id}/response(D-06)", async () => {
    const { client, flow } = makeFlow(async () => ({ latch: {} }));
    await flow.undo();
    expect(client.call).toHaveBeenCalledWith("POST", `/v1/latches/${LATCH_ID}/response/undo`, {
      body: {},
    });
  });

  it("pending中の二重送信を防ぐ(ボタンはdisabled・完了で戻る)", async () => {
    let resolveCall;
    const client = {
      call: vi.fn(
        () => new Promise((resolve) => { resolveCall = resolve; }),
      ),
    };
    const flow = createRespondFlow({
      client, latchId: LATCH_ID, refresh: vi.fn(async () => {}), showError: vi.fn(),
    });
    const buttons = [document.createElement("button")];
    const first = flow.submit("yes", { buttons });
    expect(buttons[0].disabled).toBe(true); // 送信中はdisabled
    const second = await flow.submit("yes", { buttons }); // pending中の2回目は無視
    expect(second).toEqual({ ok: false, kind: "pending" });
    expect(client.call).toHaveBeenCalledTimes(1);
    resolveCall({});
    const result = await first;
    expect(buttons[0].disabled).toBe(false);
    expect(result).toEqual({ ok: true });
  });

  it("成功(200)時は詳細を取り直す(refresh)・ok:true", async () => {
    const { flow, refresh, showError, buttons } = makeFlow(async () => ({ latch: {} }));
    const result = await flow.submit("yes", { buttons });
    expect(result).toEqual({ ok: true });
    expect(refresh).toHaveBeenCalledTimes(1);
    expect(showError).not.toHaveBeenCalled();
  });

  it("409 LATCH_EXPIREDは再取得で収束(文言を重ねない)・conflictを返す", async () => {
    const { flow, refresh, showError, buttons } = makeFlow(() => {
      throw apiError(409, "LATCH_EXPIRED");
    });
    const result = await flow.submit("yes", { buttons });
    expect(result).toEqual({ ok: false, kind: "conflict" });
    expect(refresh).toHaveBeenCalledTimes(1);
    expect(showError).not.toHaveBeenCalled();
  });

  it("409 ALREADY_ANSWERED・LATCH_CLOSEDも再取得で収束", async () => {
    const { flow, refresh, buttons } = makeFlow(() => {
      throw apiError(409, "ALREADY_ANSWERED");
    });
    await flow.submit("defer", { buttons });
    expect(refresh).toHaveBeenCalledTimes(1);
    const undo = makeFlow(() => {
      throw apiError(409, "LATCH_CLOSED");
    });
    const result = await undo.flow.undo();
    expect(result).toEqual({ ok: false, kind: "conflict" });
    expect(undo.refresh).toHaveBeenCalledTimes(1);
  });

  it("429 RATE_LIMITEDは既存文言を表示", async () => {
    const { flow, refresh, showError, buttons } = makeFlow(() => {
      throw apiError(429, "RATE_LIMITED");
    });
    await flow.submit("yes", { buttons });
    expect(showError).toHaveBeenCalledWith(RATE_LIMIT_TEXT);
    expect(refresh).not.toHaveBeenCalled();
  });

  it("未知のエラーはメッセージを表示(VALIDATION_ERROR相当の既定表示)", async () => {
    const { flow, showError, buttons } = makeFlow(() => {
      throw apiError(422, "VALIDATION_ERROR");
    });
    await flow.submit("yes", { buttons });
    expect(showError).toHaveBeenCalledTimes(1);
  });
});
