// プッシュ通知の登録フロー(05 v0.9・V-01)。Service Worker登録→トークン取得→
// POST /v1/users/me/push-token(登録upsert)までの1連を担う。
//
// トークン取得の2経路(05 v0.9 設計判断):
// - 実FCM資材(VAPID鍵)がある環境: Push API標準のsubscribeで登録要素を得る
//   (FCM SDK経由のトークン取得はws-9で資材確定後に載せ替える)
// - 資材のない環境(開発・E2E): stub-プレフィックスの疑似トークンを生成して
//   登録往復を完結させる(登録経路の試験可能性を資材待ちから分離)
//
// 許可がgrantedでない場合は登録しない(D-18: 拒否時はアプリ内通知へ到達。
// 本モジュールは何もしない=フォールバックは既存のお知らせ経路が担う)。
// browser依存(serviceWorker・Notification・crypto.getRandomValues)は引数注入
// (happy-domに実装がないため・PermissionControlと同型のDI)。

export const PUSH_SW_PATH = "latch-push-sw.js";

export const readPushConfig = () => globalThis.__LATCH_PUSH__ ?? null;

export function makeStubToken(random = Math.random) {
  const hex = Array.from({ length: 8 }, () =>
    Math.floor(random() * 256)
      .toString(16)
      .padStart(2, "0"),
  ).join("");
  return `stub-${hex}`;
}

export async function ensurePushRegistration({
  client,
  notificationApi = typeof Notification !== "undefined" ? Notification : undefined,
  serviceWorkerContainer =
    typeof navigator !== "undefined" ? navigator.serviceWorker : undefined,
  config = readPushConfig(),
  makeToken = makeStubToken,
}) {
  if (notificationApi == null || serviceWorkerContainer == null) {
    return { status: "unavailable" };
  }
  if (notificationApi.permission !== "granted") {
    return { status: "no-permission", permission: notificationApi.permission };
  }
  let token;
  try {
    const registration = await serviceWorkerContainer.register(PUSH_SW_PATH);
    if (config?.vapidKey) {
      const existing = await registration.pushManager.getSubscription();
      const subscription =
        existing ??
        (await registration.pushManager.subscribe({
          userVisibleOnly: true,
          applicationServerKey: config.vapidKey,
        }));
      token = subscription.endpoint;
    } else {
      token = makeToken(); // 資材のない環境の疑似トークン(登録往復用)
    }
  } catch {
    return { status: "error" }; // SW登録・subscribe失敗(登録せず諦める)
  }
  try {
    const res = await client.call("POST", "/v1/users/me/push-token", {
      body: { token, platform: "web" },
    });
    return { status: "registered", tokenLast4: res.push_token.token_last4 };
  } catch {
    return { status: "error" };
  }
}
