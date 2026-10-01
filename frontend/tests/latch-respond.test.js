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
  const buttons = ["yes", "defer", "no"].map((value) => {
    const button = document.createElement("button");
    button.dataset.value = value;
    return button;
  });
  return { client, refresh, showError, flow, buttons };
};

describe("回答flow(design §2.5)", () => {
  it("3択のUI値→API値をbodyへ送る(yes/defer/no)", async () => {
    const { client, flow, buttons } = makeFlow(async () => ({ latch: {} }));
    await flow.submit("yes", buttons);
    await flow.submit("defer", buttons);
    await flow.submit("no", buttons);
    expect(client.mock.calls[0]).toEqual([
      "POST",
      `/v1/latches/${LATCH_ID}/response`,
      { body: { response: "yes" } },
    ]);
    expect(client.mock.calls[1][2]).toEqual({ body: { response: "defer" } });
    expect(client.mock.calls[2][2]).toEqual({ body: { response: "no" } });
  });

  it("pending中の二重送信を防ぐ(ボタンはdisabled)", async () => {
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
    const first = flow.submit("yes", buttons);
    expect(buttons[0].disabled).toBe(true); // 送信中はdisabled
    await flow.submit("yes", buttons); // pending中の2回目は無視
    expect(client.call).toHaveBeenCalledTimes(1);
    resolveCall({});
    await first;
  });

  it("成功(200)時は詳細を取り直す(refresh)", async () => {
    const { flow, refresh, showError, buttons } = makeFlow(async () => ({ latch: {} }));
    await flow.submit("yes", buttons);
    expect(refresh).toHaveBeenCalledTimes(1);
    expect(showError).not.toHaveBeenCalled();
  });

  it("409 LATCH_EXPIREDは再取得で収束(文言を重ねない)", async () => {
    const { flow, refresh, showError, buttons } = makeFlow(() => {
      throw apiError(409, "LATCH_EXPIRED");
    });
    await flow.submit("yes", buttons);
    expect(refresh).toHaveBeenCalledTimes(1);
    expect(showError).not.toHaveBeenCalled();
  });

  it("409 ALREADY_ANSWEREDは再取得で収束", async () => {
    const { flow, refresh, buttons } = makeFlow(() => {
      throw apiError(409, "ALREADY_ANSWERED");
    });
    await flow.submit("yes", buttons);
    expect(refresh).toHaveBeenCalledTimes(1);
  });

  it("409 LATCH_CLOSEDは再取得で収束", async () => {
    const { flow, refresh, buttons } = makeFlow(() => {
      throw apiError(409, "LATCH_CLOSED");
    });
    await flow.submit("yes", buttons);
    expect(refresh).toHaveBeenCalledTimes(1);
  });

  it("429 RATE_LIMITEDは既存文言を表示", async () => {
    const { flow, refresh, showError, buttons } = makeFlow(() => {
      throw apiError(429, "RATE_LIMITED");
    });
    await flow.submit("yes", buttons);
    expect(showError).toHaveBeenCalledWith(RATE_LIMIT_TEXT);
    expect(refresh).not.toHaveBeenCalled();
  });

  it("未知のエラーはメッセージを表示(VALIDATION_ERROR相当の既定表示)", async () => {
    const { flow, showError, buttons } = makeFlow(() => {
      throw apiError(422, "VALIDATION_ERROR");
    });
    await flow.submit("yes", buttons);
    expect(showError).toHaveBeenCalledTimes(1);
  });
});
