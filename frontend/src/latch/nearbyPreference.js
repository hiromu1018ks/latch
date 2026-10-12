// nearby設定の端末既定(03 v0.6・P-04)。設定画面のトグルがONのとき、以後保存する
// 新規Intentのnotification_levelをnearby_alsoとして送る。既存Intent・visibility
// には遡及しない。サーバ側のusers列・APIは不変(05は値域不変)=端末保存のみ。
import { DEFAULT_NOTIFICATION_LEVEL } from "../intent/format.js";

export const NEARBY_STORAGE_KEY = "latch-nearby";

export const readNearbyEnabled = (storage = globalThis.localStorage) =>
  storage?.getItem(NEARBY_STORAGE_KEY) === "1";

export const writeNearbyEnabled = (storage, enabled) => {
  if (enabled) storage?.setItem(NEARBY_STORAGE_KEY, "1");
  else storage?.removeItem(NEARBY_STORAGE_KEY);
};

export const notificationLevelFor = (nearbyEnabled) =>
  nearbyEnabled ? "nearby_also" : DEFAULT_NOTIFICATION_LEVEL;
