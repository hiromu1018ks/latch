// 保存API接続(active/draft)とエラー表示マップ(design §2.9)。
// 検証の強制はサーバ側(03 §3)− フロントはcanSubmit/canDraftで事前にブロックし、
// サーバ422/429/503はcodeで分岐して表示位置へ振り分ける(確定値13)。
import { buildCreateRequest } from "./format.js";
import { canDraft, canSubmit } from "./state.js";

// design §2.9のエラー表示マップ。VALIDATION_ERRORのみmessageの先頭一致で
// 表示位置を推定する(確定値13「messageは参考にしか使わない」の唯一の例外。
// 未知の形式はグローバル表示へ落ちる — Review Focus 6)
export const errorPlacement = (err) => {
  switch (err?.code) {
    case "GEOCODING_FAILED":
      return { target: "location", note: "場所が見つかりません。条件リストの場所を修正してください。" };
    case "UNDER_AGE":
      return { target: "global", note: "飲酒を含むIntentは20歳以上の方のみ作成できます。" };
    case "ACTIVE_INTENT_LIMIT":
      return {
        target: "global",
        note: "預けられるIntentはActive 5件までです。停止中・期限切れのIntentを確認してください。",
      };
    case "RATE_LIMITED":
      return { target: "global", note: "操作が集中しています。少し時間をおいてもう一度お試しください。" };
    case "LLM_UNAVAILABLE":
    case "DEPENDENCY_UNAVAILABLE":
      return { target: "global", note: "ただいま混み合っています。もう一度お試しください。" };
    case "VALIDATION_ERROR": {
      const message = err.message ?? "";
      if (message.startsWith("time.start") || message.startsWith("expires_at")) {
        return { target: "time", note: "時刻を修正してください(現在〜7日以内)。" };
      }
      return { target: "global", note: "入力内容を確認してください。" };
    }
    default:
      return { target: "global", note: "保存できませんでした。もう一度お試しください。" };
  }
};

export const createSaveFlow = ({
  client,
  getState,
  getOptions,
  onBusy,
  onActiveSaved,
  onDraftSaved,
  onFormError,
}) => {
  let pending = false;

  const post = async (status, onSuccess) => {
    if (pending) return; // 二重送信防止(design §2.9)
    const state = getState();
    if (status === "active" && !canSubmit(state)) return;
    if (status === "draft" && !canDraft(state)) return;

    pending = true;
    onBusy?.(true);
    try {
      const body = buildCreateRequest(state, { status, ...getOptions() });
      await client.call("POST", "/v1/intents", { body });
      onSuccess();
    } catch (err) {
      if (err?.code === "UNAUTHENTICATED") return; // パネル表示はonSessionExpired側で処理済み
      onFormError?.(errorPlacement(err));
    } finally {
      pending = false;
      onBusy?.(false);
    }
  };

  return {
    saveActive: () => post("active", onActiveSaved),
    saveDraft: () => post("draft", onDraftSaved),
  };
};
