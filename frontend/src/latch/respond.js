// 回答flow(M3 ws-7 design §2.5+UI v2 Phase 3・D-05〜D-07)。2択→POST response、
// undo→POST response/undo。pending制御・成功も409系も「詳細再取得で現在の姿へ
// 収束」の一本(表示の唯一の真実は詳細応答)。追加の文言を重ねない。
// 見送り理由(D-07)はdefer回答のbodyにのみ受理される(05 §5)ため、
// 04のdeclined状態で選んだchipsをsubmit時にいっしょに送る。
// 戻り値は{ok, kind}: 409系(conflict)の再取得失敗なし・429(rate)・その他(error)を
// 呼び出し側(detail)がトースト等に振り分けられるようにしている。
import { RATE_LIMIT_TEXT } from "./texts.js";

export const createRespondFlow = ({ client, latchId, refresh, showError }) => {
  let pending = false;

  const setDisabled = (buttons, value) => {
    for (const button of buttons) button.disabled = value;
  };

  const call = async (path, body, buttons) => {
    pending = true;
    try {
      await client.call("POST", `/v1/latches/${latchId}${path}`, body);
      await refresh();
      return { ok: true };
    } catch (err) {
      // 409 LATCH_EXPIRED / ALREADY_ANSWERED / LATCH_CLOSED / UNANSWERED はすべて
      // 再取得の結果(不成立姿・回答済み表示)をそのまま表示する
      if (err?.status === 409) {
        await refresh();
        return { ok: false, kind: "conflict" };
      }
      if (err?.code === "RATE_LIMITED") {
        showError(RATE_LIMIT_TEXT);
        return { ok: false, kind: "rate" };
      }
      showError(err?.message ?? "通信エラーが発生しました。");
      return { ok: false, kind: "error" };
    } finally {
      pending = false;
      setDisabled(buttons, false);
    }
  };

  return {
    get busy() {
      return pending;
    },
    submit: (value, { reasons = null, buttons = [] } = {}) => {
      if (pending) return Promise.resolve({ ok: false, kind: "pending" });
      setDisabled(buttons, true);
      const body = { body: { response: value } };
      // 見送り理由(D-07): defer時のみ受理・任意複数(値はtime/place/size/mood)
      if (value === "defer" && reasons?.length) body.body.reasons = [...reasons];
      return call("/response", body, buttons);
    },
    undo: (buttons = []) => {
      if (pending) return Promise.resolve({ ok: false, kind: "pending" });
      setDisabled(buttons, true);
      return call("/response/undo", { body: {} }, buttons);
    },
  };
};
