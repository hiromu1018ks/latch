// 同意フローのvitest(M4 ws-5 計画Task 9)。文言はtexts.js定数・APIは
// client.callのスタブ。happy-domのDOMで描画を検証する(permission.js試験と同型)。
import { afterEach, describe, expect, it, vi } from "vitest";
import {
  CONSENT_AGREE_LABEL,
  CONSENT_CHECK_LABEL,
  CONSENT_CONDITION_TEXT,
  CONSENT_DESTINATIONS_TEXT,
  CONSENT_LEAD_TEXT,
  CONSENT_NO_TRAINING_TEXT,
  CONSENT_REQUIRED_TOAST_TEXT,
  CONSENT_SCOPE_TEXT,
  CONSENT_STATE_NOT_AGREED_TEXT,
  CONSENT_TOAST_TEXT,
} from "../src/latch/texts.js";
import { createConsent } from "../src/latch/consent.js";

const NOW_ISO = "2026-10-03T12:00:00+00:00";

const mountConsent = async ({ consented_at = null, postError = false } = {}) => {
  const onboarding = document.createElement("div");
  const settings = document.createElement("div");
  const client = {
    call: vi.fn(async (method, path) => {
      if (method === "GET" && path === "/v1/users/me/consent") {
        return { consented_at };
      }
      if (method === "POST" && path === "/v1/users/me/consent") {
        if (postError) throw new Error("network");
        return { consented_at: NOW_ISO };
      }
      throw new Error(`unexpected ${method} ${path}`);
    }),
  };
  const chrome = { showToast: vi.fn() };
  const consent = createConsent({ client, chrome, el: { onboarding, settings } });
  return { consent, client, chrome, onboarding, settings };
};

// notifyRequiredはfetchしないため同期待ち(chromeのトーストとhash遷移のみ検証)
const mountConsentSync = () => {
  const onboarding = document.createElement("div");
  const settings = document.createElement("div");
  const client = { call: vi.fn() };
  const chrome = { showToast: vi.fn() };
  const consent = createConsent({ client, chrome, el: { onboarding, settings } });
  return { consent, chrome };
};

afterEach(() => {
  window.location.hash = "";
});

describe("オンボーディング画面(design §2.4)", () => {
  it("v2部品: card+ctaで構成する(UI v2 Phase 5・文言は承認済み定数のまま)", async () => {
    const { consent, onboarding } = await mountConsent({ consented_at: null });
    await consent.show();
    expect(onboarding.querySelector(".card.consent-card")).not.toBeNull();
    const button = onboarding.querySelector("[data-role=consent-agree]");
    expect(button.className).toContain("cta"); // 同意する=v2のcta(58px系pill)
    // チェックを入れるとctaが有効化される(acc色はCSS側・disabledはロジック側)
    const check = onboarding.querySelector("[data-role=consent-check]");
    check.checked = true;
    check.dispatchEvent(new Event("change"));
    expect(button.disabled).toBe(false);
  });

  it("文言表示: 承認済み文言の全定数が載る(題は画面側h1が担うためカードには出ない)", async () => {
    const { consent, onboarding } = await mountConsent();
    await consent.show();
    const text = onboarding.textContent;
    expect(onboarding.querySelector(".consent-title")).toBeNull();
    expect(text).toContain(CONSENT_LEAD_TEXT);
    expect(text).toContain(CONSENT_DESTINATIONS_TEXT);
    expect(text).toContain(CONSENT_SCOPE_TEXT);
    expect(text).toContain(CONSENT_NO_TRAINING_TEXT);
    expect(text).toContain(CONSENT_CONDITION_TEXT);
  });

  it("未同意: チェックボックスとボタンがあり・未チェック時はボタン無効", async () => {
    const { consent, onboarding } = await mountConsent({ consented_at: null });
    await consent.show();
    const check = onboarding.querySelector("[data-role=consent-check]");
    const button = onboarding.querySelector("[data-role=consent-agree]");
    expect(check).not.toBeNull();
    expect(button.disabled).toBe(true);
    check.checked = true;
    check.dispatchEvent(new Event("change"));
    expect(button.disabled).toBe(false);
    expect(button.textContent).toBe(CONSENT_AGREE_LABEL);
    expect(onboarding.textContent).toContain(CONSENT_CHECK_LABEL);
    expect(onboarding.textContent).toContain(CONSENT_STATE_NOT_AGREED_TEXT);
  });

  it("同意済み: 操作なし・状態と日時を表示", async () => {
    const { consent, onboarding } = await mountConsent({ consented_at: NOW_ISO });
    await consent.show();
    expect(onboarding.querySelector("[data-role=consent-agree]")).toBeNull();
    expect(onboarding.textContent).toContain("同意済み");
  });

  it("POST成功: 同意APIを叩き・トースト・ホームへ戻る", async () => {
    const { consent, client, chrome, onboarding } = await mountConsent();
    await consent.show();
    const check = onboarding.querySelector("[data-role=consent-check]");
    check.checked = true;
    check.dispatchEvent(new Event("change"));
    onboarding.querySelector("[data-role=consent-agree]").click();
    await Promise.resolve();
    await Promise.resolve();
    await Promise.resolve();
    expect(client.call).toHaveBeenCalledWith("POST", "/v1/users/me/consent");
    expect(chrome.showToast).toHaveBeenCalledWith(CONSENT_TOAST_TEXT);
    expect(onboarding.textContent).toContain("同意済み");
    expect(window.location.hash).toBe("#/");
  });

  it("POST失敗: ボタンを再試行可能なまま戻す", async () => {
    const { consent, onboarding } = await mountConsent({ postError: true });
    await consent.show();
    const check = onboarding.querySelector("[data-role=consent-check]");
    check.checked = true;
    check.dispatchEvent(new Event("change"));
    const button = onboarding.querySelector("[data-role=consent-agree]");
    button.click();
    await Promise.resolve();
    await Promise.resolve();
    await Promise.resolve();
    expect(button.disabled).toBe(false);
    expect(onboarding.textContent).toContain(CONSENT_STATE_NOT_AGREED_TEXT);
  });
});

describe("設定画面第3セクション(design §2.4・D-14「設定画面から常に再確認できる」)", () => {
  it("loadは設定mountへ文言と状態を描く(操作要素なし・題はセクションh2が担う)", async () => {
    const { consent, settings } = await mountConsent({ consented_at: NOW_ISO });
    await consent.load();
    expect(settings.querySelector(".consent-title")).toBeNull();
    expect(settings.textContent).toContain("同意済み");
    expect(settings.querySelector("button")).toBeNull();
  });

  it("v2部品: card構成+状態線にdata-agreed(UI v2 Phase 5)", async () => {
    const { consent, settings } = await mountConsent({ consented_at: NOW_ISO });
    await consent.load();
    expect(settings.querySelector(".card.consent-card")).not.toBeNull();
    expect(settings.querySelector("[data-role=consent-state]").dataset.agreed).toBe("true");
  });
});

describe("誘導経路2本(design §2.4)", () => {
  it("ensureGuidance: 未同意なら #/onboarding へ遷移", async () => {
    const { consent } = await mountConsent({ consented_at: null });
    await consent.ensureGuidance();
    expect(window.location.hash).toBe("#/onboarding");
  });

  it("ensureGuidance: 同意済みなら遷移しない", async () => {
    const { consent } = await mountConsent({ consented_at: NOW_ISO });
    window.location.hash = "#/";
    await consent.ensureGuidance();
    expect(window.location.hash).toBe("#/");
  });

  it("notifyRequired: トースト+ #/onboarding へ遷移(422検出の後段)", () => {
    const { consent, chrome } = mountConsentSync();
    consent.notifyRequired();
    expect(chrome.showToast).toHaveBeenCalledWith(CONSENT_REQUIRED_TOAST_TEXT);
    expect(window.location.hash).toBe("#/onboarding");
  });
});
