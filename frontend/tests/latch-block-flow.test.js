import { describe, expect, it, vi } from "vitest";
import {
  BLOCK_CONFIRM_TEXT,
  BLOCK_SELECT_ERROR_TEXT,
  BLOCK_TOAST_TEXT,
  RATE_LIMIT_TEXT,
} from "../src/latch/texts.js";
import { createBlockFlow } from "../src/latch/blockFlow.js";

const LATCH_ID = "11111111-1111-4111-8111-111111111111";
const ME = "22222222-2222-4222-8222-222222222222";
const PEER = "33333333-3333-4333-8333-333333333333";
const THIRD = "44444444-4444-4444-8444-444444444444";
const apiError = (status, code) =>
  Object.assign(new Error("x"), { name: "ApiError", status, code });

const makeFlow = (callImpl) => {
  const client = { call: vi.fn(callImpl ?? (async () => ({ blocked_id: PEER }))) };
  const chrome = { showToast: vi.fn() };
  const onBlocked = vi.fn(async () => {});
  const modal = document.createElement("div");
  modal.hidden = true;
  const flow = createBlockFlow({ client, chrome, modal, onBlocked });
  return { client, chrome, onBlocked, modal, flow };
};

// happy-domはラジオの排他(uncheck)を実装しないため全解除→選択(latch-reportと同型)
const selectBlockee = (modal, value) => {
  for (const radio of modal.querySelectorAll('input[name="blockee"]')) {
    radio.checked = radio.value === value;
  }
};

describe("ブロック登録flow(design §2.7・§5-1承認)", () => {
  it("1対1: 相手固定で確認文言を出しPOST /v1/users/{相手}/block(パス指定・bodyなし)", async () => {
    const { client, modal, flow } = makeFlow();
    flow.open({
      participants: [
        { user_id: ME, display_name: "自分", profile: {} },
        { user_id: PEER, display_name: "相手", profile: {} },
      ],
      meId: ME,
    });
    expect(modal.hidden).toBe(false);
    expect(modal.textContent).toContain("相手"); // 対象表示
    expect(modal.textContent).toContain(BLOCK_CONFIRM_TEXT);
    await flow.submit();
    expect(client.call).toHaveBeenCalledWith("POST", `/v1/users/${PEER}/block`);
  });

  it("グループ: 対象者を選択して選択値のパスへPOST", async () => {
    const { client, modal, flow } = makeFlow();
    flow.open({
      participants: [
        { user_id: ME, display_name: "自分", profile: {} },
        { user_id: PEER, display_name: "相手A", profile: {} },
        { user_id: THIRD, display_name: "相手B", profile: {} },
      ],
      meId: ME,
    });
    selectBlockee(modal, THIRD);
    await flow.submit();
    expect(client.call).toHaveBeenCalledWith("POST", `/v1/users/${THIRD}/block`);
  });

  it("成功(201冪等)でモーダル閉鎖+トースト+onBlocked(詳細再取得)", async () => {
    const { chrome, onBlocked, modal, flow } = makeFlow();
    flow.open({
      participants: [
        { user_id: ME, display_name: "自分", profile: {} },
        { user_id: PEER, display_name: "相手", profile: {} },
      ],
      meId: ME,
    });
    await flow.submit();
    expect(modal.hidden).toBe(true);
    expect(chrome.showToast).toHaveBeenCalledWith(BLOCK_TOAST_TEXT);
    expect(onBlocked).toHaveBeenCalledTimes(1);
  });

  it("グループで未選択は送信せず案内(422を出さない)", async () => {
    const { client, modal, flow } = makeFlow();
    flow.open({
      participants: [
        { user_id: ME, display_name: "自分", profile: {} },
        { user_id: PEER, display_name: "相手A", profile: {} },
        { user_id: THIRD, display_name: "相手B", profile: {} },
      ],
      meId: ME,
    });
    await flow.submit(); // 何も選んでいない
    expect(client.call).not.toHaveBeenCalled();
    expect(modal.hidden).toBe(false); // 閉じない
    expect(modal.textContent).toContain(BLOCK_SELECT_ERROR_TEXT);
  });

  it("失敗(429)はモーダルを閉じずRATE_LIMIT_TEXT", async () => {
    const { client, modal, flow } = makeFlow(() => {
      throw apiError(429, "RATE_LIMITED");
    });
    flow.open({
      participants: [
        { user_id: ME, display_name: "自分", profile: {} },
        { user_id: PEER, display_name: "相手", profile: {} },
      ],
      meId: ME,
    });
    await flow.submit();
    expect(modal.hidden).toBe(false);
    expect(modal.textContent).toContain(RATE_LIMIT_TEXT);
    expect(client.call).toHaveBeenCalledTimes(1);
  });
});
