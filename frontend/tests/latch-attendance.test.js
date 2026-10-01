import { describe, expect, it, vi } from "vitest";
import { ApiError } from "../src/api/session.js";
import {
  ATTENDANCE_ALREADY_TEXT,
  ATTENDANCE_DONE_TEXT,
  ATTENDANCE_QUESTION,
} from "../src/latch/texts.js";
import { createAttendanceFlow } from "../src/latch/attendance.js";

const LATCH_ID = "11111111-1111-4111-8111-111111111111";
const apiError = (status, code) => new ApiError(status, code, "msg");

const mountFlow = (callImpl) => {
  const client = { call: vi.fn(callImpl) };
  const mount = document.createElement("div");
  const flow = createAttendanceFlow({ client, latchId: LATCH_ID, mount });
  return { client, mount, flow };
};

describe("attendance(D-09・design §2.7)", () => {
  it("質問と2択([会いました]/[会えていません])を描画する", () => {
    const { mount, flow } = mountFlow(async () => ({}));
    flow.renderQuestion();
    expect(mount.textContent).toContain(ATTENDANCE_QUESTION);
    const labels = [...mount.querySelectorAll("button")].map((b) => b.textContent);
    expect(labels).toEqual(["会いました", "会えていません"]);
  });

  it("[会いました]は{attended:true}を送る(ボタンclick経由)", async () => {
    const { client, mount, flow } = mountFlow(async () => ({
      latch_id: LATCH_ID, actual_attended: true,
    }));
    flow.renderQuestion();
    mount.querySelectorAll("button")[0].click(); // 会いました
    await Promise.resolve(); await Promise.resolve(); await Promise.resolve();
    expect(client.call).toHaveBeenCalledWith(
      "POST",
      `/v1/latches/${LATCH_ID}/attendance`,
      { body: { attended: true } },
    );
  });

  it("[会えていません]は{attended:false}を送る", async () => {
    const client = { call: vi.fn(async () => ({ latch_id: LATCH_ID, actual_attended: false })) };
    const mount = document.createElement("div");
    const flow = createAttendanceFlow({ client, latchId: LATCH_ID, mount });
    flow.renderQuestion();
    mount.querySelectorAll("button")[1].click(); // 会えていません
    await Promise.resolve(); await Promise.resolve(); await Promise.resolve();
    expect(client.call).toHaveBeenCalledWith(
      "POST",
      `/v1/latches/${LATCH_ID}/attendance`,
      { body: { attended: false } },
    );
  });

  it("200で「回答を送りました」表示(2択は消す)", async () => {
    const { mount, flow } = mountFlow(async () => ({
      latch_id: LATCH_ID, actual_attended: true,
    }));
    flow.renderQuestion();
    await flow.submit(true);
    expect(mount.textContent).toContain(ATTENDANCE_DONE_TEXT);
    expect(mount.querySelectorAll("button").length).toBe(0);
  });

  it("409 ATTENDANCE_ALREADY_SUBMITTEDで回答済み表示に切替", async () => {
    const { mount, flow } = mountFlow(() => {
      throw apiError(409, "ATTENDANCE_ALREADY_SUBMITTED");
    });
    flow.renderQuestion();
    await flow.submit(true);
    expect(mount.textContent).toContain(ATTENDANCE_ALREADY_TEXT);
    expect(mount.querySelectorAll("button").length).toBe(0);
  });

  it("409 ATTENDANCE_WINDOW_CLOSEDで非表示化(3日窓はサーバ判定)", async () => {
    const { mount, flow } = mountFlow(() => {
      throw apiError(409, "ATTENDANCE_WINDOW_CLOSED");
    });
    flow.renderQuestion();
    await flow.submit(true);
    expect(mount.children.length).toBe(0); // 完全に非表示(§9-6①)
  });
});
