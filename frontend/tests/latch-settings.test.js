import { afterEach, describe, expect, it, vi } from "vitest";
import { cleanup } from "@testing-library/react";
import { act } from "react";
import {
  PERMISSION_DEFAULT_TEXT,
  PERMISSION_DENIED_TEXT,
  PERMISSION_GRANTED_TEXT,
  PERMISSION_NOTE_TEXT,
  PERMISSION_REQUEST_LABEL,
  PERMISSION_UNSUPPORTED_TEXT,
} from "../src/latch/texts.js";
import {
  createPermissionControl,
  permissionText,
} from "../src/latch/permission.js";
import { createSettings } from "../src/latch/settings.js";
import { renderApp } from "./helpers.jsx";

const mountPermission = (notificationApi) => {
  const mount = document.createElement("div");
  const control = createPermissionControl({ notificationApi, mount });
  return { mount, control };
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

describe("createPermissionControl(design §2.5・D-18)", () => {
  it("v2部品: アイコン枠(permission-row)+ghost pillで構成する(UI v2 Phase 5)", () => {
    const { mount, control } = mountPermission({
      permission: "default",
      requestPermission: vi.fn(async () => "granted"),
    });
    control.render();
    expect(mount.querySelector(".permission-row .privacy-ic")).not.toBeNull();
    expect(mount.querySelector("[data-role=permission-request]").className)
      .toContain("ghost");
  });

  it("granted: 状態文言+補足文のみ(ボタンなし)", () => {
    const { mount, control } = mountPermission({ permission: "granted" });
    control.render();
    expect(mount.textContent).toContain(PERMISSION_GRANTED_TEXT);
    expect(mount.textContent).toContain(PERMISSION_NOTE_TEXT); // D-18フォールバック保証
    expect(mount.querySelector("button")).toBeNull();
  });

  it("default: [通知の許可を求める]ボタンを置く", () => {
    const { mount, control } = mountPermission({
      permission: "default",
      requestPermission: vi.fn(async () => "granted"),
    });
    control.render();
    expect(mount.textContent).toContain(PERMISSION_DEFAULT_TEXT);
    expect(mount.querySelector("[data-role=permission-request]").textContent)
      .toBe(PERMISSION_REQUEST_LABEL);
  });

  it("denied: 案内文言のみ(再要求APIが効かないため操作なし)", () => {
    const { mount, control } = mountPermission({ permission: "denied" });
    control.render();
    expect(mount.textContent).toContain(PERMISSION_DENIED_TEXT);
    expect(mount.querySelector("button")).toBeNull();
  });

  it("API不在(非対応環境): 非対応文言(操作なし)", () => {
    const { mount, control } = mountPermission(undefined);
    control.render();
    expect(mount.textContent).toContain(PERMISSION_UNSUPPORTED_TEXT);
    expect(mount.querySelector("button")).toBeNull();
  });

  it("default→requestPermissionがgrantedを返せば再描画でgranted文言へ", async () => {
    const api = {
      permission: "default",
      requestPermission: vi.fn(async () => "granted"),
    };
    const { mount, control } = mountPermission(api);
    control.render();
    mount.querySelector("[data-role=permission-request]").click();
    await Promise.resolve();
    await Promise.resolve();
    await Promise.resolve();
    expect(api.requestPermission).toHaveBeenCalledTimes(1);
    expect(mount.textContent).toContain(PERMISSION_GRANTED_TEXT);
  });

  it("requestPermissionの拒否(default戻り)と例外はdefault維持(再表示)", async () => {
    // 拒否: 戻り値"default"
    const denied = {
      permission: "default",
      requestPermission: vi.fn(async () => "default"),
    };
    const a = mountPermission(denied);
    a.control.render();
    a.mount.querySelector("[data-role=permission-request]").click();
    await Promise.resolve();
    await Promise.resolve();
    await Promise.resolve();
    expect(a.mount.textContent).toContain(PERMISSION_DEFAULT_TEXT);
    // 例外
    const throwing = {
      permission: "default",
      requestPermission: vi.fn(() => Promise.reject(new Error("x"))),
    };
    const b = mountPermission(throwing);
    b.control.render();
    b.mount.querySelector("[data-role=permission-request]").click();
    await Promise.resolve();
    await Promise.resolve();
    await Promise.resolve();
    expect(b.mount.textContent).toContain(PERMISSION_DEFAULT_TEXT);
  });
});

describe("settings画面骨組(design §2.4・UI v2 Phase 6からApp描画)", () => {
  it("showはpermissionControl.render・blocks.load・consent.loadを呼ぶ", () => {
    const permissionControl = { render: vi.fn() };
    const blocks = { load: vi.fn(async () => {}) };
    const consent = { load: vi.fn(async () => {}) };
    const settings = createSettings({ permissionControl, blocks, consent });
    settings.show();
    expect(permissionControl.render).toHaveBeenCalledTimes(1);
    expect(blocks.load).toHaveBeenCalledTimes(1);
    expect(consent.load).toHaveBeenCalledTimes(1);
  });

  it("App描画: 設定3カード・解除dialog・お知らせpopover・オンボーディングがルートごとに組める", async () => {
    renderApp();
    const go = async (hash) => {
      window.location.hash = hash;
      window.dispatchEvent(new Event("hashchange"));
      await act(async () => {});
    };
    // ヘッダー側(常在): 通知/アカウントpopover・解除dialog
    for (const id of [
      "settingsButton", "noticeList", "noticeMoreButton",
      // UI v2 Phase 5: ブロック解除の確認は旧modal-backdropからunblockDialogへ
      "notificationDot", "unblockDialog",
    ]) {
      expect(document.getElementById(id)).not.toBeNull();
    }
    expect(document.getElementById("notificationDot").hidden).toBe(true);
    // 旧モーダル方式は廃止(<dialog class="dlg">へ統一)
    expect(document.getElementById("unblockModal")).toBeNull();
    expect(document.querySelector(".modal-backdrop")).toBeNull();
    // 候補・成立タブは一覧ルートへ(D-11)
    expect(document.querySelector('a[data-nav="proposals"]').getAttribute("href")).toBe("#/proposals");
    expect(document.querySelector('a[data-nav="matched"]').getAttribute("href")).toBe("#/matched");
    // 設定画面(3カード)
    await go("#/settings");
    for (const id of [
      "settingsScreen", "permissionControl", "blockList", "blockMoreButton",
      "consentControl", "consentSettingsTitle",
    ]) {
      expect(document.getElementById(id)).not.toBeNull();
    }
    // オンボーディング
    await go("#/onboarding");
    for (const id of ["onboardingScreen", "consentOnboarding"]) {
      expect(document.getElementById(id)).not.toBeNull();
    }
    // UI v2 Phase 4(D-09): blockModalはsafetyDialogへ統合(詳細画面が担う)
    await go("#/latches/11111111-1111-4111-8111-111111111111");
    expect(document.getElementById("safetyDialog")).not.toBeNull();
  });

  it("App配線: hash遷移で設定画面へ入る(3カードのマウント点が揃う)", async () => {
    renderApp();
    window.location.hash = "#/settings";
    window.dispatchEvent(new Event("hashchange"));
    await act(async () => {});
    expect(document.getElementById("settingsScreen").hidden).toBe(false);
    expect(document.getElementById("permissionControl").childElementCount).toBeGreaterThan(0);
  });

  it("router: 候補・成立の一覧ルートを解析する(D-11)", async () => {
    const { parseHash } = await import("../src/router.js");
    expect(parseHash("#/proposals")).toEqual({ name: "proposals", id: null, query: {} });
    expect(parseHash("#/matched")).toEqual({ name: "matched", id: null, query: {} });
  });
});
