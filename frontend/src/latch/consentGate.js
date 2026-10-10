// LLM同意フローの横断ゲート(M4 ws-5・UI v2 Phase 6)。誘導経路2本を
// フレームワーク非依存の関数として切り出したもの(Appのclientラップと起動時
// 初期化から呼ぶ。旧consent.jsのensureGuidance/notifyRequiredと同じ挙動)。
import { CONSENT_REQUIRED_TOAST_TEXT } from "./texts.js";

// 誘導経路(b): 422 CONSENT_REQUIRED検出時(トースト+オンボーディングへ)
export const notifyConsentRequired = (showToast) => {
  showToast(CONSENT_REQUIRED_TOAST_TEXT);
  location.hash = "#/onboarding";
};

// 誘導経路(a): 未同意ならオンボーディングへ
export const ensureConsentGuidance = async (client) => {
  const current = await client.call("GET", "/v1/users/me/consent"); // design §2.4④
  if (current.consented_at == null) location.hash = "#/onboarding";
};
