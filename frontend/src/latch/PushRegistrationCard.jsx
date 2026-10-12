// プッシュ登録状態の表示(05 v0.9・V-01)。設定画面の通知セクション内で
// 許可状態に応じた登録フロー(ensurePushRegistration)を起動し、結果を表示する。
// 表示のみ・再試行ボタン等の操作導線は持たない(許可の取り直しは
// PermissionControlが担うため)。browser依存は注入(happy-dom対策・DI)。
import { useEffect, useState } from "react";
import { ensurePushRegistration } from "./pushRegistration.js";
import {
  PUSH_ERROR_TEXT,
  PUSH_NO_PERMISSION_TEXT,
  PUSH_REGISTERED_TEXT,
  PUSH_UNAVAILABLE_TEXT,
} from "./texts.js";
import { Icon } from "../ui/Icon.jsx";

export const PushRegistrationCard = ({
  client,
  notificationApi,
  serviceWorkerContainer,
  config,
}) => {
  const [state, setState] = useState(null);

  useEffect(() => {
    let alive = true;
    ensurePushRegistration({
      client,
      notificationApi,
      serviceWorkerContainer,
      config,
    }).then((result) => {
      if (alive) setState(result);
    });
    return () => {
      alive = false;
    };
  }, [client, notificationApi, serviceWorkerContainer, config]);

  const text =
    state == null
      ? "" // 起動中(一瞬・空文字で埋める)
      : state.status === "registered"
        ? `${PUSH_REGISTERED_TEXT}(識別子 …${state.tokenLast4})`
        : state.status === "no-permission"
          ? PUSH_NO_PERMISSION_TEXT
          : state.status === "unavailable"
            ? PUSH_UNAVAILABLE_TEXT
            : PUSH_ERROR_TEXT;

  return (
    <div className="permission-row" data-role="push-registration">
      <span className="privacy-ic" aria-hidden="true">
        <Icon name="bell" size={19} />
      </span>
      <p className="permission-status" data-role="push-registration-status">
        {text}
      </p>
    </div>
  );
};
