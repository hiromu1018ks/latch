// 回答flow(M3 ws-7 design §2.5)。3択→POST response・pending制御・
// 成功も409系も「詳細再取得で現在の姿へ収束」の一本(表示の唯一の真実は
// 詳細応答)。追加の文言を重ねない。
import { RATE_LIMIT_TEXT } from "./texts.js";

export const createRespondFlow = ({ client, latchId, refresh, showError }) => {
  let pending = false;
  return {
    submit: async (value, buttons = []) => {
      if (pending) return;
      pending = true;
      for (const button of buttons) button.disabled = true;
      try {
        await client.call("POST", `/v1/latches/${latchId}/response`, {
          body: { response: value },
        });
        await refresh();
      } catch (err) {
        // 409 LATCH_EXPIRED / ALREADY_ANSWERED / LATCH_CLOSED はすべて
        // 再取得の結果(不成立姿・回答済み表示)をそのまま表示する
        if (err?.status === 409) {
          await refresh();
          return;
        }
        if (err?.code === "RATE_LIMITED") {
          showError(RATE_LIMIT_TEXT);
          return;
        }
        showError(err?.message ?? "通信エラーが発生しました。");
      } finally {
        pending = false;
        for (const button of buttons) button.disabled = false;
      }
    },
  };
};
