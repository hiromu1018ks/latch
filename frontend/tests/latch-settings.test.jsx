import { afterEach, describe, expect, it, vi } from "vitest";
import { act, cleanup, fireEvent, render, waitFor } from "@testing-library/react";
import {
  PERMISSION_DEFAULT_TEXT,
  PERMISSION_DENIED_TEXT,
  PERMISSION_GRANTED_TEXT,
  PERMISSION_NOTE_TEXT,
  PERMISSION_REQUEST_LABEL,
  PERMISSION_UNSUPPORTED_TEXT,
} from "../src/latch/texts.js";
import {
  PermissionControl,
  permissionText,
} from "../src/latch/PermissionControl.jsx";
import { SettingsScreen } from "../src/latch/SettingsScreen.jsx";
import { ChromeProvider } from "../src/ui/chrome.jsx";

// 旧permission.js/settings.js(素のDOM配線)の試験を、Reactコンポーネント
// (PermissionControl/SettingsScreen・UI v2 Phase 6)のRTLへ書き換えたもの。
// 意図=文言写像・4状態の操作有無・3カードの構成・解除dialogは踏襲する。

const mountPermission = (notificationApi) => {
  render(<PermissionControl notificationApi={notificationApi} />);
};

afterEach(() => {
  cleanup();
});

describe("permissionText(状態→文言写像・design §2.5)", () => {
  it("4状態+API不在の文言", () => {
    expect(permissionText("granted")).toBe(PERMISSION_GRANTED_TEXT);
    expect(permissionText("default")).toBe(PERMISSION_DEFAULT_TEXT);
    expect(permissionText("denied")).toBe(PERMISSION_DENIED_TEXT);
    expect(permissionText(undefined)).toBe(PERMISSION_UNSUPPORTED_TEXT);
  });
});

describe("PermissionControl(design §2.5・D-18)", () => {
  it("v2部品: アイコン枠(permission-row)+ghost pillで構成する(UI v2 Phase 5)", () => {
    mountPermission({
      permission: "default",
      requestPermission: vi.fn(async () => "granted"),
    });
    expect(document.querySelector(".permission-row .privacy-ic")).not.toBeNull();
    expect(document.querySelector("[data-role=permission-request]").className)
      .toContain("ghost");
  });

  it("granted: 状態文言+補足文のみ(ボタンなし)", () => {
    mountPermission({ permission: "granted" });
    expect(document.body.textContent).toContain(PERMISSION_GRANTED_TEXT);
    expect(document.body.textContent).toContain(PERMISSION_NOTE_TEXT); // D-18フォールバック保証
    expect(document.querySelector("button")).toBeNull();
  });

  it("default: [通知の許可を求める]ボタンを置く", () => {
    mountPermission({
      permission: "default",
      requestPermission: vi.fn(async () => "granted"),
    });
    expect(document.body.textContent).toContain(PERMISSION_DEFAULT_TEXT);
    expect(document.querySelector("[data-role=permission-request]").textContent)
      .toBe(PERMISSION_REQUEST_LABEL);
  });

  it("denied: 案内文言のみ(再要求APIが効かないため操作なし)", () => {
    mountPermission({ permission: "denied" });
    expect(document.body.textContent).toContain(PERMISSION_DENIED_TEXT);
    expect(document.querySelector("button")).toBeNull();
  });

  it("API不在(非対応環境): 非対応文言(操作なし)", () => {
    mountPermission(undefined);
    expect(document.body.textContent).toContain(PERMISSION_UNSUPPORTED_TEXT);
    expect(document.querySelector("button")).toBeNull();
  });

  it("default→requestPermissionがgrantedを返せば再描画でgranted文言へ", async () => {
    const api = {
      permission: "default",
      requestPermission: vi.fn(async () => "granted"),
    };
    mountPermission(api);
    fireEvent.click(document.querySelector("[data-role=permission-request]"));
    await waitFor(() => {
      expect(api.requestPermission).toHaveBeenCalledTimes(1);
      expect(document.querySelector("[data-role=permission-status]").textContent).toBe(
        PERMISSION_GRANTED_TEXT,
      );
    });
  });

  it("requestPermissionの拒否(default戻り)と例外はdefault維持(再表示)", async () => {
    // 拒否: 戻り値"default"
    const denied = {
      permission: "default",
      requestPermission: vi.fn(async () => "default"),
    };
    render(<PermissionControl notificationApi={denied} />);
    fireEvent.click(document.querySelector("[data-role=permission-request]"));
    await waitFor(() => {
      expect(denied.requestPermission).toHaveBeenCalledTimes(1);
      expect(document.querySelector("[data-role=permission-status]").textContent).toBe(
        PERMISSION_DEFAULT_TEXT,
      );
    });
    cleanup();
    // 例外
    const throwing = {
      permission: "default",
      requestPermission: vi.fn(() => Promise.reject(new Error("x"))),
    };
    render(<PermissionControl notificationApi={throwing} />);
    fireEvent.click(document.querySelector("[data-role=permission-request]"));
    await waitFor(() => {
      expect(throwing.requestPermission).toHaveBeenCalledTimes(1);
      expect(document.querySelector("[data-role=permission-status]").textContent).toBe(
        PERMISSION_DEFAULT_TEXT,
      );
    });
  });
});

describe("SettingsScreen(3カードの構成・UI v2 Phase 6からReact)", () => {
  const mountSettings = (callImpl) => {
    cleanup();
    const client = { call: vi.fn(callImpl ?? (async () => ({ items: [], next_cursor: null, consented_at: "2026-10-03T12:00:00+00:00" }))) };
    render(
      <ChromeProvider>
        <SettingsScreen client={client} />
      </ChromeProvider>,
    );
    return { client };
  };

  it("ヒーロー+3カード(通知・ブロック管理・LLM送信の同意)を描く", async () => {
    mountSettings();
    await waitFor(() => {
      expect(document.querySelector(".settings-hero .settings-title").textContent).toBe("設定");
      expect(document.getElementById("notificationSettingsTitle").textContent).toBe("通知");
      expect(document.getElementById("blockSettingsTitle").textContent).toBe("ブロック管理");
      expect(document.getElementById("consentSettingsTitle").textContent).toBe("LLM送信の同意");
    });
    // 各セクションの中身が揃う(通知=permission・ブロック=一覧・同意=card)
    expect(document.querySelector(".permission-row")).not.toBeNull();
    expect(document.getElementById("blockList")).not.toBeNull();
    expect(document.querySelector(".consent-card")).not.toBeNull();
    // 解除の確認dialog(BlocksSectionが担う)
    expect(document.getElementById("unblockDialog").getAttribute("aria-labelledby")).toBe("unblockTitle");
  });

  it("ブロックのloadと同意のloadが走る(3セクションの初期化)", async () => {
    const { client } = mountSettings();
    await waitFor(() => {
      const paths = client.call.mock.calls.map(([, path]) => path);
      expect(paths).toContain("/v1/users/me/blocks?limit=20");
      expect(paths).toContain("/v1/users/me/consent");
    });
  });
});
