import { afterEach, describe, expect, it, vi } from "vitest";
import { cleanup, fireEvent, render, waitFor } from "@testing-library/react";
import { ApiError } from "../src/api/session.js";
import {
  ATTENDANCE_ALREADY_TEXT,
  ATTENDANCE_DONE_TEXT,
  ATTENDANCE_NO_LABEL,
  ATTENDANCE_QUESTION,
  ATTENDANCE_YES_LABEL,
} from "../src/latch/texts.js";
import { AttendanceCard } from "../src/latch/AttendanceCard.jsx";

// 旧createAttendanceFlow(素のDOM配線)の試験を、Reactコンポーネント
// (AttendanceCard・UI v2 Phase 6)のRTLへ書き換えたもの。意図=文言・2択の
// クラス・送信ボディ・409の表示切替・3日窓超過の非表示化は踏襲する。
const LATCH_ID = "11111111-1111-4111-8111-111111111111";
const apiError = (status, code) => new ApiError(status, code, "msg");

const mountCard = (callImpl) => {
  const client = { call: vi.fn(callImpl) };
  render(<AttendanceCard client={client} latchId={LATCH_ID} />);
  return { client };
};

afterEach(() => {
  cleanup();
});

describe("attendance(D-09・v2様式化=D-14)", () => {
  it("質問と2択(会いました=cta・会えていません=ghost)を描画する", () => {
    mountCard(async () => ({}));
    const wrap = document.querySelector(".lt-attendance");
    expect(wrap.hidden).toBe(false); // asideのattendance枠を可視化
    expect(wrap.textContent).toContain(ATTENDANCE_QUESTION);
    const buttons = [...wrap.querySelectorAll("button")];
    expect(buttons.map((b) => b.textContent)).toEqual([
      ATTENDANCE_YES_LABEL,
      ATTENDANCE_NO_LABEL,
    ]);
    expect(buttons[0].className).toContain("attendance-yes");
    expect(buttons[1].className).toContain("attendance-no");
  });

  it("[会いました]は{attended:true}を送る(ボタンclick経由)", async () => {
    const { client } = mountCard(async () => ({
      latch_id: LATCH_ID, actual_attended: true,
    }));
    fireEvent.click(document.querySelector(".attendance-yes"));
    await waitFor(() => {
      expect(client.call).toHaveBeenCalledWith(
        "POST",
        `/v1/latches/${LATCH_ID}/attendance`,
        { body: { attended: true } },
      );
    });
  });

  it("[会えていません]は{attended:false}を送る", async () => {
    const { client } = mountCard(async () => ({
      latch_id: LATCH_ID, actual_attended: false,
    }));
    fireEvent.click(document.querySelector(".attendance-no"));
    await waitFor(() => {
      expect(client.call).toHaveBeenCalledWith(
        "POST",
        `/v1/latches/${LATCH_ID}/attendance`,
        { body: { attended: false } },
      );
    });
  });

  it("200で「回答を送りました」表示(2択は消す)", async () => {
    mountCard(async () => ({
      latch_id: LATCH_ID, actual_attended: true,
    }));
    fireEvent.click(document.querySelector(".attendance-yes"));
    await waitFor(() => {
      expect(document.querySelector(".lt-attendance").textContent).toContain(ATTENDANCE_DONE_TEXT);
    });
    expect(document.querySelectorAll(".lt-attendance button").length).toBe(0);
  });

  it("409 ATTENDANCE_ALREADY_SUBMITTEDで回答済み表示に切替", async () => {
    mountCard(() => {
      throw apiError(409, "ATTENDANCE_ALREADY_SUBMITTED");
    });
    fireEvent.click(document.querySelector(".attendance-yes"));
    await waitFor(() => {
      expect(document.querySelector(".lt-attendance").textContent).toContain(ATTENDANCE_ALREADY_TEXT);
    });
    expect(document.querySelectorAll(".lt-attendance button").length).toBe(0);
  });

  it("409 ATTENDANCE_WINDOW_CLOSEDで非表示化(3日窓はサーバ判定)", async () => {
    mountCard(() => {
      throw apiError(409, "ATTENDANCE_WINDOW_CLOSED");
    });
    fireEvent.click(document.querySelector(".attendance-yes"));
    await waitFor(() => {
      expect(document.querySelector(".lt-attendance").children.length).toBe(0); // 完全に非表示(§9-6①)
    });
  });
});
