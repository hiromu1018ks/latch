import { afterEach, describe, expect, it, vi } from "vitest";
import { cleanup, fireEvent, render, waitFor } from "@testing-library/react";
import { ApiError } from "../src/api/session.js";
import {
  RATE_LIMIT_TEXT,
  REPORT_REASON_OPTIONS,
  SAFETY_NOTE_TEXT,
} from "../src/latch/texts.js";
import { SafetyDialog } from "../src/latch/SafetyDialog.jsx";

// 旧createSafetyFlow(素のDOM配線)の試験を、Reactコンポーネント
// (SafetyDialog・UI v2 Phase 6)のRTLへ書き換えたもの。意図=2ステップ(D-09)・
// 対象ラジオ・reportee_idの扱い(案X)・done状態のrole=status・エラー表示を踏襲。
const LATCH_ID = "11111111-1111-4111-8111-111111111111";
const ME = "22222222-2222-4222-8222-222222222222";
const PEER = "33333333-3333-4333-8333-333333333333";
const THIRD = "44444444-4444-4444-8444-444444444444";

const ME_PEER = [
  { user_id: ME, display_name: "自分", profile: {} },
  { user_id: PEER, display_name: "相手", profile: {} },
];

const mountDialog = (callImpl, { participants = ME_PEER, onBlocked } = {}) => {
  const client = { call: vi.fn(callImpl ?? (async () => ({ report_id: "r1" }))) };
  const onBlockedFn = onBlocked ?? vi.fn(async () => {});
  const onClose = vi.fn();
  render(
    <SafetyDialog
      client={client}
      open
      onClose={onClose}
      onBlocked={onBlockedFn}
      latchId={LATCH_ID}
      participants={participants}
      meId={ME}
    />,
  );
  return { client, onBlocked: onBlockedFn, onClose, dialog: () => document.getElementById("safetyDialog") };
};

// happy-domはラジオの排他(uncheck)を実装しないため、対象を明示的にclickする
// (Reactのvalue trackerが手動checked+changeを抑止するためfireEvent.clickで選ぶ)
const pick = (dialog, name, value) => {
  fireEvent.click(dialog.querySelector(`input[name="${name}"][value="${value}"]`));
};

afterEach(() => {
  cleanup();
});

describe("通報・ブロック統合dialog(D-09・プロトタイプ05)", () => {
  it("成立済み1対1: 対象は相手で選択済み・ブロックでPOST /v1/users/{id}/block", async () => {
    const { client, dialog } = mountDialog(async () => ({ blocked_id: PEER }));
    expect(dialog().open).toBe(true);
    expect(dialog().querySelector("h2").textContent).toBe("通報・ブロック");
    expect(dialog().textContent).toContain("相手"); // 対象ラジオ(選択済み)
    expect(dialog().querySelector('input[name="safety-who"]').checked).toBe(true);
    expect(dialog().textContent).toContain(SAFETY_NOTE_TEXT);
    fireEvent.click(dialog().querySelector("[data-role=safety-block]"));
    await waitFor(() => {
      expect(client.call).toHaveBeenCalledWith("POST", `/v1/users/${PEER}/block`);
    });
    await waitFor(() => {
      expect(dialog().querySelector("[role=status]").textContent)
        .toBe("相手さんをブロックしました。今後この相手とLATCHが成立することはありません。");
    });
  });

  it("ブロック成功でonBlocked(詳細再取得)が走る", async () => {
    const { dialog, onBlocked } = mountDialog(async () => ({ blocked_id: PEER }));
    fireEvent.click(dialog().querySelector("[data-role=safety-block]"));
    await waitFor(() => expect(onBlocked).toHaveBeenCalledTimes(1));
  });

  it("グループ: 未選択では両ボタン無効・選択で有効化して選択値へPOST", async () => {
    const { client, dialog } = mountDialog(async () => ({ blocked_id: THIRD }), {
      participants: [
        { user_id: ME, display_name: "自分", profile: {} },
        { user_id: PEER, display_name: "相手A", profile: {} },
        { user_id: THIRD, display_name: "相手B", profile: {} },
      ],
    });
    expect(dialog().querySelector("[data-role=safety-block]").disabled).toBe(true);
    expect(dialog().querySelector("[data-role=safety-report]").disabled).toBe(true);
    pick(dialog(), "safety-who", THIRD);
    expect(dialog().querySelector("[data-role=safety-block]").disabled).toBe(false);
    fireEvent.click(dialog().querySelector("[data-role=safety-block]"));
    await waitFor(() => {
      expect(client.call).toHaveBeenCalledWith("POST", `/v1/users/${THIRD}/block`);
    });
  });

  it("通報は理由ステップ(D-09)を挿みreportee_id明示でPOST /v1/reports", async () => {
    const { client, dialog } = mountDialog();
    fireEvent.click(dialog().querySelector("[data-role=safety-report]"));
    // 理由4択のステップがdialog内に出る(対象ステップとは差し替わる)
    expect(dialog().querySelectorAll('input[name="safety-reason"]').length).toBe(4);
    expect(dialog().querySelector("[data-role=safety-report-submit]").disabled).toBe(true);
    pick(dialog(), "safety-reason", "other");
    expect(dialog().querySelector("[data-role=safety-report-submit]").disabled).toBe(false);
    fireEvent.click(dialog().querySelector("[data-role=safety-report-submit]"));
    await waitFor(() => {
      expect(client.call).toHaveBeenCalledWith("POST", "/v1/reports", {
        body: { latch_id: LATCH_ID, reason: "other", reportee_id: PEER },
      });
    });
    await waitFor(() => {
      expect(dialog().querySelector("[role=status]").textContent)
        .toBe("相手さんについての通報を受け付けました。運営が内容を確認します。");
    });
  });

  it("理由の4値コードがそのままAPI値になる", async () => {
    const client = { call: vi.fn(async () => ({ report_id: "r1" })) };
    for (const [code] of REPORT_REASON_OPTIONS) {
      const onBlocked = vi.fn(async () => {});
      render(
        <SafetyDialog
          client={client}
          open
          onClose={vi.fn()}
          onBlocked={onBlocked}
          latchId={LATCH_ID}
          participants={null}
          meId={ME}
        />,
      );
      const dialog = () => document.getElementById("safetyDialog");
      fireEvent.click(dialog().querySelector("[data-role=safety-report]"));
      pick(dialog(), "safety-reason", code);
      fireEvent.click(dialog().querySelector("[data-role=safety-report-submit]"));
      await waitFor(() => {
        expect(client.call.mock.calls.at(-1)[2].body.reason).toBe(code);
      });
      cleanup(); // done状態から次の周回へ(openし直し)
    }
  });

  it("提案1対1(participants=null): 対象ラジオなし・ブロックなし・reportee_id省略body(案X)", async () => {
    const { client, dialog } = mountDialog(undefined, { participants: null });
    expect(dialog().querySelector(".safety-targets")).toBeNull(); // 対象表示なし(非開示)
    expect(dialog().querySelector("[data-role=safety-block]")).toBeNull();
    expect(dialog().querySelector("[data-role=safety-report]").disabled).toBe(false);
    fireEvent.click(dialog().querySelector("[data-role=safety-report]"));
    pick(dialog(), "safety-reason", "unpleasant_behavior");
    fireEvent.click(dialog().querySelector("[data-role=safety-report-submit]"));
    await waitFor(() => {
      expect(client.call).toHaveBeenCalledWith("POST", "/v1/reports", {
        body: { latch_id: LATCH_ID, reason: "unpleasant_behavior" },
      });
    });
    await waitFor(() => {
      expect(dialog().querySelector("[role=status]").textContent)
        .toBe("通報を受け付けました。運営が内容を確認します。");
    });
  });

  it("理由未選択では送信ボタンが無効でAPIを呼ばない(旧試験の直接押下はReactの制御に置き換え)", async () => {
    const { client, dialog } = mountDialog(undefined, { participants: null });
    fireEvent.click(dialog().querySelector("[data-role=safety-report]"));
    const submit = dialog().querySelector("[data-role=safety-report-submit]");
    expect(submit.disabled).toBe(true); // 未選択では無効(理由必須のガード)
    fireEvent.click(submit);
    expect(client.call).not.toHaveBeenCalled();
  });

  it("理由ステップのもどるで対象ステップへ戻る", () => {
    const { dialog } = mountDialog();
    fireEvent.click(dialog().querySelector("[data-role=safety-report]"));
    expect(dialog().querySelector("[data-role=safety-back]")).not.toBeNull();
    fireEvent.click(dialog().querySelector("[data-role=safety-back]"));
    expect(dialog().querySelector("[data-role=safety-block]")).not.toBeNull();
  });

  it("失敗(429)はdialogを開いたままRATE_LIMIT_TEXT", async () => {
    const apiError = Object.assign(new Error("x"), {
      name: "ApiError", status: 429, code: "RATE_LIMITED",
    });
    const { dialog } = mountDialog(() => {
      throw apiError;
    });
    fireEvent.click(dialog().querySelector("[data-role=safety-block]"));
    await waitFor(() => {
      expect(dialog().querySelector("[data-role=safety-error]").textContent)
        .toBe(RATE_LIMIT_TEXT);
    });
    expect(dialog().open).toBe(true);
  });

  it("done状態の閉じるでdialogを閉じる(onCloseが呼ばれる)", async () => {
    const { dialog, onClose } = mountDialog(undefined, { participants: null });
    fireEvent.click(dialog().querySelector("[data-role=safety-report]"));
    pick(dialog(), "safety-reason", "other");
    fireEvent.click(dialog().querySelector("[data-role=safety-report-submit]"));
    await waitFor(() => {
      expect(dialog().querySelector("[data-role=safety-close]")).not.toBeNull();
    });
    fireEvent.click(dialog().querySelector("[data-role=safety-close]"));
    expect(onClose).toHaveBeenCalledTimes(1);
  });
});
