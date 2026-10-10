// 通知許可コントロール(M3 ws-8 design §2.5・D-18)。ブラウザNotification API
// は直接参照せず注入する(happy-domにNotificationがないため試験必須のDI・
// client注入と同型)。実送信(FCM web)はM4 — この画面はブラウザの許可状態の
// 表示と取り直しのみ(トークン登録を伴わないためサーバ通信は発生しない)。
import {
  PERMISSION_DEFAULT_TEXT,
  PERMISSION_DENIED_TEXT,
  PERMISSION_GRANTED_TEXT,
  PERMISSION_NOTE_TEXT,
  PERMISSION_REQUEST_LABEL,
  PERMISSION_UNSUPPORTED_TEXT,
} from "./texts.js";
import { escapeHtml } from "./view.js";
import { iconHtml } from "../ui/icons.js";

export const permissionText = (permission) =>
  permission === "granted"
    ? PERMISSION_GRANTED_TEXT
    : permission === "default"
      ? PERMISSION_DEFAULT_TEXT
      : permission === "denied"
        ? PERMISSION_DENIED_TEXT
        : PERMISSION_UNSUPPORTED_TEXT; // API不在(非対応環境)・未知値

export const createPermissionControl = ({ notificationApi, mount }) => {
  let permission = notificationApi?.permission; // 初期スナップショット

  const render = () => {
    // granted・denied・API不在は操作なし(再要求が効くのはdefaultのみ)。
    // v2部品(02のprivacy-icアイコン枠+ghost pill)で構成(UI v2 Phase 5)
    const buttonHtml =
      permission === "default"
        ? `<button type="button" class="ghost permission-request" data-role="permission-request">${PERMISSION_REQUEST_LABEL}</button>`
        : "";
    mount.innerHTML = `
      <div class="permission-row">
        <span class="privacy-ic" aria-hidden="true">${iconHtml("bell", 19)}</span>
        <p class="permission-status" data-role="permission-status">${escapeHtml(
          permissionText(permission),
        )}</p>
      </div>
      ${buttonHtml}
      <p class="permission-note">${escapeHtml(PERMISSION_NOTE_TEXT)}</p>
    `;
    const button = mount.querySelector("[data-role=permission-request]");
    if (button) {
      button.addEventListener("click", async () => {
        button.disabled = true;
        try {
          permission = await notificationApi.requestPermission();
        } catch {
          // 拒否・失敗は現在の状態を維持(再表示)
        }
        render();
      });
    }
  };

  return { render };
};
