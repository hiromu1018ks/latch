// nearby設定トグル(03 v0.6・P-04)。設定画面の通知セクション内。
// ON=以後の新規Intent保存時notification_level=nearby_also(端末保存・既存Intent
// には遡及しない)。server APIは不変(05は値域不変)。
import { useState } from "react";
import {
  readNearbyEnabled,
  writeNearbyEnabled,
} from "./nearbyPreference.js";
import { NEARBY_SETTING_LABEL, NEARBY_SETTING_NOTE } from "./texts.js";

export const NearbyToggle = ({ storage = globalThis.localStorage }) => {
  const [enabled, setEnabled] = useState(() => readNearbyEnabled(storage));
  const toggle = () => {
    const next = !enabled;
    writeNearbyEnabled(storage, next);
    setEnabled(next);
  };
  return (
    <div className="permission-row" data-role="nearby-setting">
      <div>
        {/* 入力をaria-hidden装飾枠の外へ置く(focusable要素を隠さない) */}
        <input
          type="checkbox"
          id="nearbyToggle"
          role="switch"
          aria-describedby="nearbyToggleNote"
          checked={enabled}
          onChange={toggle}
          data-role="nearby-toggle"
        />{" "}
        <label htmlFor="nearbyToggle" className="permission-status">
          {NEARBY_SETTING_LABEL}
        </label>
        <p className="permission-note" id="nearbyToggleNote">
          {NEARBY_SETTING_NOTE}
        </p>
      </div>
    </div>
  );
};
