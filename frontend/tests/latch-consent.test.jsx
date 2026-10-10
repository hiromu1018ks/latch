import { afterEach, describe, expect, it, vi } from "vitest";
import { act, cleanup, fireEvent, render, waitFor } from "@testing-library/react";
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
import { ConsentCard } from "../src/latch/ConsentCard.jsx";
import {
  ensureConsentGuidance,
  notifyConsentRequired,
} from "../src/latch/consentGate.js";
import { ToastArea } from "../src/ui/chrome.jsx";
import { ChromeProvider } from "../src/ui/chrome.jsx";

// 旧createConsent(素のDOM配線)の試験を、Reactコンポーネント(ConsentCard・
// consentGate・UI v2 Phase 6)のRTLへ書き換えたもの。文言はtexts.jsの
// CONSENT_*定数(2026-10-03ユーザー承認済み)で不変。
const NOW_ISO = "2026-10-03T12:00:00+00:00";

const mountCard = ({ consented_at = null, postError = false, interactive = true } = {}) => {
  cleanup();
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
  const hashSpy = vi.fn();
  render(
    <ChromeProvider>
      <ConsentCard client={client} interactive={interactive} onAgreed={hashSpy} />
      <ToastArea />
    </ChromeProvider>,
  );
  return { client, onAgreed: hashSpy };
};

afterEach(() => {
  cleanup();
  window.location.hash = "";
});

describe("オンボーディング(design §2.4)", () => {
  it("v2部品: card+ctaで構成する(UI v2 Phase 5・文言は承認済み定数のまま)", async () => {
    mountCard({ consented_at: null });
    await waitFor(() => expect(document.querySelector(".card.consent-card")).not.toBeNull());
    const button = document.querySelector("[data-role=consent-agree]");
    expect(button.className).toContain("cta"); // 同意する=v2のcta(58px系pill)
    // チェックを入れるとctaが有効化される(acc色はCSS側・disabledはロジック側)
    fireEvent.click(document.querySelector(".consent-check input"));
    await act(async () => {});
    expect(button.disabled).toBe(false);
  });

  it("文言表示: 承認済み文言の全定数が載る(題は画面側h1が担うためカードには出ない)", async () => {
    mountCard();
    await waitFor(() => expect(document.querySelector(".consent-card")).not.toBeNull());
    const text = document.querySelector(".consent-card").textContent;
    expect(document.querySelector(".consent-title")).toBeNull();
    expect(text).toContain(CONSENT_LEAD_TEXT);
    expect(text).toContain(CONSENT_DESTINATIONS_TEXT);
    expect(text).toContain(CONSENT_SCOPE_TEXT);
    expect(text).toContain(CONSENT_NO_TRAINING_TEXT);
    expect(text).toContain(CONSENT_CONDITION_TEXT);
  });

  it("未同意: チェックボックスとボタンがあり・未チェック時はボタン無効", async () => {
    mountCard({ consented_at: null });
    await waitFor(() => expect(document.querySelector("[data-role=consent-agree]")).not.toBeNull());
    const check = document.querySelector(".consent-check input");
    const button = document.querySelector("[data-role=consent-agree]");
    expect(button.disabled).toBe(true);
    fireEvent.click(check);
    await act(async () => {});
    expect(button.disabled).toBe(false);
    expect(button.textContent).toBe(CONSENT_AGREE_LABEL);
    expect(document.querySelector(".consent-card").textContent).toContain(CONSENT_CHECK_LABEL);
    expect(document.querySelector(".consent-card").textContent).toContain(CONSENT_STATE_NOT_AGREED_TEXT);
  });

  it("同意済み: 操作なし・状態と日時を表示", async () => {
    mountCard({ consented_at: NOW_ISO });
    await waitFor(() => expect(document.querySelector(".consent-card")).not.toBeNull());
    expect(document.querySelector("[data-role=consent-agree]")).toBeNull();
    expect(document.querySelector(".consent-card").textContent).toContain("同意済み");
  });

  it("POST成功: 同意APIを叩き・トースト・onAgreed(ホームへ戻す)", async () => {
    const { client, onAgreed } = mountCard();
    await waitFor(() => expect(document.querySelector("[data-role=consent-agree]")).not.toBeNull());
    fireEvent.click(document.querySelector(".consent-check input"));
    await act(async () => {});
    fireEvent.click(document.querySelector("[data-role=consent-agree]"));
    await waitFor(() => {
      expect(client.call).toHaveBeenCalledWith("POST", "/v1/users/me/consent");
      expect(document.querySelector(".toast-msg--plain").textContent).toBe(CONSENT_TOAST_TEXT);
      expect(document.querySelector(".consent-card").textContent).toContain("同意済み");
      expect(onAgreed).toHaveBeenCalledTimes(1); // オンボーディングは #/ へ戻す
    });
  });

  it("POST失敗: ボタンを再試行可能なまま戻す", async () => {
    mountCard({ postError: true });
    await waitFor(() => expect(document.querySelector("[data-role=consent-agree]")).not.toBeNull());
    fireEvent.click(document.querySelector(".consent-check input"));
    await act(async () => {});
    fireEvent.click(document.querySelector("[data-role=consent-agree]"));
    await waitFor(() => {
      expect(document.querySelector("[data-role=consent-agree]").disabled).toBe(false);
      expect(document.querySelector(".consent-card").textContent).toContain(CONSENT_STATE_NOT_AGREED_TEXT);
    });
  });
});

describe("設定画面第3セクション(design §2.4・D-14「設定画面から常に再確認できる」)", () => {
  it("静的表示(interactive=false): 文言と状態を描き操作要素はない(題はセクションh2が担う)", async () => {
    mountCard({ consented_at: NOW_ISO, interactive: false });
    await waitFor(() => expect(document.querySelector(".consent-card")).not.toBeNull());
    expect(document.querySelector(".consent-title")).toBeNull();
    expect(document.querySelector(".consent-card").textContent).toContain("同意済み");
    expect(document.querySelector(".consent-card button")).toBeNull();
  });

  it("v2部品: card構成+状態線にdata-agreed(UI v2 Phase 5)", async () => {
    mountCard({ consented_at: NOW_ISO, interactive: false });
    await waitFor(() => expect(document.querySelector(".card.consent-card")).not.toBeNull());
    expect(document.querySelector("[data-role=consent-state]").dataset.agreed).toBe("true");
  });
});

describe("誘導経路2本(design §2.4・consentGate)", () => {
  it("ensureConsentGuidance: 未同意なら #/onboarding へ遷移", async () => {
    const client = { call: vi.fn(async () => ({ consented_at: null })) };
    await ensureConsentGuidance(client);
    expect(window.location.hash).toBe("#/onboarding");
  });

  it("ensureConsentGuidance: 同意済みなら遷移しない", async () => {
    window.location.hash = "#/";
    const client = { call: vi.fn(async () => ({ consented_at: NOW_ISO })) };
    await ensureConsentGuidance(client);
    expect(window.location.hash).toBe("#/");
  });

  it("notifyConsentRequired: トースト+ #/onboarding へ遷移(422検出の後段)", () => {
    const showToast = vi.fn();
    notifyConsentRequired(showToast);
    expect(showToast).toHaveBeenCalledWith(CONSENT_REQUIRED_TOAST_TEXT);
    expect(window.location.hash).toBe("#/onboarding");
  });
});
