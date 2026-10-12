// プッシュ登録フロー(pushRegistration.js・05 v0.9・V-01)のunit試験。
// browser依存(serviceWorker・Notification)は注入スタブで検証する
// (happy-domに実装がないため・PermissionControlと同型のDI)。
import { afterEach, describe, expect, it, vi } from "vitest";
import { cleanup } from "@testing-library/react";
import {
  PUSH_SW_PATH,
  ensurePushRegistration,
  makeStubToken,
} from "../src/latch/pushRegistration.js";
import {
  readNearbyEnabled,
  notificationLevelFor,
  writeNearbyEnabled,
} from "../src/latch/nearbyPreference.js";
import { PushRegistrationCard } from "../src/latch/PushRegistrationCard.jsx";
import { NearbyToggle } from "../src/latch/NearbyToggle.jsx";
import { render, waitFor } from "@testing-library/react";
import { ChromeProvider } from "../src/ui/chrome.jsx";

const memoryStorage = () => {
  const m = new Map();
  return {
    getItem: (k) => (m.has(k) ? m.get(k) : null),
    setItem: (k, v) => m.set(k, String(v)),
    removeItem: (k) => m.delete(k),
    _map: m,
  };
};

afterEach(() => {
  cleanup();
  vi.unstubAllGlobals();
});

const swContainer = (registerImpl) => ({
  register: vi.fn(async () => ({})),
  ...(registerImpl ? { register: registerImpl } : {}),
});
const grantedApi = { permission: "granted" };
const client = (post = async () => ({ push_token: { token_last4: "ab3f", platform: "web" } })) => ({
  call: vi.fn(post),
});

describe("ensurePushRegistration(05 v0.9)", () => {
  it("granted+資材なし: SW登録→stubトークン→POST /v1/users/me/push-token→registered", async () => {
    const c = client();
    const result = await ensurePushRegistration({
      client: c,
      notificationApi: grantedApi,
      serviceWorkerContainer: swContainer(),
      config: null,
    });
    expect(result).toEqual({ status: "registered", tokenLast4: "ab3f" });
    expect(c.call).toHaveBeenCalledWith("POST", "/v1/users/me/push-token", {
      body: { token: expect.stringMatching(/^stub-[0-9a-f]{16}$/), platform: "web" },
    });
  });

  it("granted+VAPID鍵あり: pushManager.subscribeのendpointをトークンに使う", async () => {
    const c = client();
    const endpoint = "https://push.example/sub/XYZ";
    const registration = {
      pushManager: {
        getSubscription: async () => null,
        subscribe: vi.fn(async () => ({ endpoint })),
      },
    };
    const result = await ensurePushRegistration({
      client: c,
      notificationApi: grantedApi,
      serviceWorkerContainer: { register: async () => registration },
      config: { vapidKey: "BExample" },
    });
    // 応答のtoken_last4はサーバ算出(スタブはab3f)。subscribe呼び出しと
    // endpointがトークンとして送られたことを検証
    expect(result).toEqual({ status: "registered", tokenLast4: "ab3f" });
    expect(c.call).toHaveBeenCalledWith("POST", "/v1/users/me/push-token", {
      body: { token: endpoint, platform: "web" },
    });
    expect(registration.pushManager.subscribe).toHaveBeenCalledWith({
      userVisibleOnly: true,
      applicationServerKey: "BExample",
    });
  });

  it("許可がgrantedでない: 登録しない(D-18=アプリ内通知へ到達・no-permission)", async () => {
    const c = client();
    const result = await ensurePushRegistration({
      client: c,
      notificationApi: { permission: "default" },
      serviceWorkerContainer: swContainer(),
    });
    expect(result.status).toBe("no-permission");
    expect(c.call).not.toHaveBeenCalled();
  });

  it("serviceWorker不在: unavailable(env非対応)", async () => {
    const result = await ensurePushRegistration({
      client: client(),
      notificationApi: grantedApi,
      serviceWorkerContainer: undefined,
    });
    expect(result).toEqual({ status: "unavailable" });
  });

  it("SW登録失敗: 登録せずerror", async () => {
    const c = client();
    const result = await ensurePushRegistration({
      client: c,
      notificationApi: grantedApi,
      serviceWorkerContainer: {
        register: async () => {
          throw new Error("sw rejected");
        },
      },
    });
    expect(result).toEqual({ status: "error" });
    expect(c.call).not.toHaveBeenCalled();
  });

  it("API失敗(401等): error(握って表示側へ)", async () => {
    const c = client(async () => {
      throw new Error("401");
    });
    const result = await ensurePushRegistration({
      client: c,
      notificationApi: grantedApi,
      serviceWorkerContainer: swContainer(),
    });
    expect(result).toEqual({ status: "error" });
  });

  it("SWの登録パスはlatch-push-sw.js", () => {
    expect(PUSH_SW_PATH).toBe("latch-push-sw.js");
  });

  it("makeStubTokenはstub-プレフィックスの16進", () => {
    const token = makeStubToken(() => 0.42);
    expect(token).toMatch(/^stub-[0-9a-f]{16}$/);
  });
});

describe("nearbyPreference(03 v0.6・P-04)", () => {
  it("既定はオフ=proposals_only", () => {
    const store = memoryStorage();
    expect(readNearbyEnabled(store)).toBe(false);
    expect(notificationLevelFor(false)).toBe("proposals_only");
  });

  it("ONのときnearby_also・書き込みはlocalStorageの'1'削除", () => {
    const store = memoryStorage();
    writeNearbyEnabled(store, true);
    expect(store._map.get("latch-nearby")).toBe("1");
    expect(readNearbyEnabled(store)).toBe(true);
    expect(notificationLevelFor(true)).toBe("nearby_also");
    writeNearbyEnabled(store, false);
    expect(store._map.has("latch-nearby")).toBe(false);
  });
});

describe("PushRegistrationCard(V-01)", () => {
  it("registered: 識別子last4つき文言を表示", async () => {
    render(
      <ChromeProvider>
        <PushRegistrationCard
          client={client()}
          notificationApi={grantedApi}
          serviceWorkerContainer={swContainer()}
          config={null}
        />
      </ChromeProvider>,
    );
    await waitFor(() => {
      expect(document.querySelector("[data-role=push-registration-status]").textContent)
        .toBe("この端末で通知を受け取ります(識別子 …ab3f)");
    });
  });

  it("no-permission: D-18のフォールバック文言", async () => {
    render(
      <ChromeProvider>
        <PushRegistrationCard
          client={client()}
          notificationApi={{ permission: "default" }}
          serviceWorkerContainer={swContainer()}
        />
      </ChromeProvider>,
    );
    await waitFor(() => {
      expect(document.querySelector("[data-role=push-registration-status]").textContent)
        .toContain("お知らせはアプリ内で届きます");
    });
  });
});

describe("NearbyToggle(03 v0.6)", () => {
  it("トグル操作でlocalStorageへ反映", async () => {
    const { fireEvent } = await import("@testing-library/react");
    const store = memoryStorage();
    render(
      <ChromeProvider>
        <NearbyToggle storage={store} />
      </ChromeProvider>,
    );
    const toggle = document.querySelector("[data-role=nearby-toggle]");
    expect(toggle.checked).toBe(false);
    fireEvent.click(toggle);
    expect(toggle.checked).toBe(true);
    expect(store._map.get("latch-nearby")).toBe("1");
    fireEvent.click(toggle);
    expect(store._map.has("latch-nearby")).toBe(false);
  });
});
