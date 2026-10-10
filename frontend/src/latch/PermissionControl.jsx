// 通知許可コントロール(M3 ws-8 design §2.5・D-18・UI v2 Phase 6でReact化)。
// 旧permission.js(素のDOM配線)をコンポーネントへ載せ替えたもの。ブラウザ
// Notification APIは直接参照せず注入する(happy-domにNotificationがないため
// 試験必須のDI・client注入と同型)。実送信(FCM web)はM4 — この画面はブラウザの
// 許可状態の表示と取り直しのみ(トークン登録を伴わないためサーバ通信は発生しない)。
import { useState } from "react";
import {
  PERMISSION_DEFAULT_TEXT,
  PERMISSION_DENIED_TEXT,
  PERMISSION_GRANTED_TEXT,
  PERMISSION_NOTE_TEXT,
  PERMISSION_REQUEST_LABEL,
  PERMISSION_UNSUPPORTED_TEXT,
} from "./texts.js";
import { Icon } from "../ui/Icon.jsx";

export const permissionText = (permission) =>
  permission === "granted"
    ? PERMISSION_GRANTED_TEXT
    : permission === "default"
      ? PERMISSION_DEFAULT_TEXT
      : permission === "denied"
        ? PERMISSION_DENIED_TEXT
        : PERMISSION_UNSUPPORTED_TEXT; // API不在(非対応環境)・未知値

export const PermissionControl = ({ notificationApi }) => {
  const [permission, setPermission] = useState(notificationApi?.permission); // 初期スナップショット
  const [busy, setBusy] = useState(false);

  const request = async () => {
    setBusy(true);
    try {
      setPermission(await notificationApi.requestPermission());
    } catch {
      // 拒否・失敗は現在の状態を維持(再表示)
    }
    setBusy(false);
  };

  // granted・denied・API不在は操作なし(再要求が効くのはdefaultのみ)。
  // v2部品(02のprivacy-icアイコン枠+ghost pill)で構成(UI v2 Phase 5)
  return (
    <div>
      <div className="permission-row">
        <span className="privacy-ic" aria-hidden="true">
          <Icon name="bell" size={19} />
        </span>
        <p className="permission-status" data-role="permission-status">
          {permissionText(permission)}
        </p>
      </div>
      {permission === "default" ? (
        <button type="button" className="ghost permission-request" data-role="permission-request" disabled={busy} onClick={request}>
          {PERMISSION_REQUEST_LABEL}
        </button>
      ) : null}
      <p className="permission-note">{PERMISSION_NOTE_TEXT}</p>
    </div>
  );
};
