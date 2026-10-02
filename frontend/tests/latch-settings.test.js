import { describe, expect, it, vi } from "vitest";
import { readFileSync } from "node:fs";
import { resolve } from "node:path";
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

const mountPermission = (notificationApi) => {
  const mount = document.createElement("div");
  const control = createPermissionControl({ notificationApi, mount });
  return { mount, control };
};

describe("permissionText(状態→文言写像・design §2.5)", () => {
  it("4状態+API不在の文言", () => {
    expect(permissionText("granted")).toBe(PERMISSION_GRANTED_TEXT);
    expect(permissionText("default")).toBe(PERMISSION_DEFAULT_TEXT);
    expect(permissionText("denied")).toBe(PERMISSION_DENIED_TEXT);
    expect(permissionText(undefined)).toBe(PERMISSION_UNSUPPORTED_TEXT);
  });
});

describe("createPermissionControl(design §2.5・D-18)", () => {
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
