// LATCH プッシュ通知用 Service Worker(05 v0.9・V-01)。
// 役割: ①push eventを汎用文でOS通知へ出す(FR-21・08 §2.6: 条件サマリは
// 絶対に本文へ載せない) ②notificationclickでアプリへ戻す(FR-46: data.latch_id
// があれば当該提案詳細04へ・なければルート)。
// 実FCM送信はws-9(資材待ち)。本SWは登録時にbrowserへ置かれ、送信手段の
// 実装状態と独立に挙動する。

self.addEventListener("push", (event) => {
  let latchId = null;
  try {
    latchId = event.data ? event.data.json().latch_id : null;
  } catch {
    latchId = null; // payload不正は汎用文のみで表示(安全側)
  }
  // 本文は汎用文で固定(08 §2.6)。条件サマリ・latch_idは本文に出さない。
  event.waitUntil(
    self.registration.showNotification("LATCH", {
      body: "LATCH候補があります。\n詳細はアプリでご確認ください。",
      tag: latchId ? `latch-${latchId}` : "latch",
      data: { latch_id: latchId },
    }),
  );
});

self.addEventListener("notificationclick", (event) => {
  event.notification.close();
  const latchId = event.notification.data && event.notification.data.latch_id;
  const target = new URL(latchId ? `#/latches/${latchId}` : "#/", self.location.origin);
  event.waitUntil(
    self.clients.matchAll({ type: "window", includeUncontrolled: true }).then((wins) => {
      for (const win of wins) {
        if (win.url.startsWith(self.location.origin)) {
          win.navigate(target.href).catch(() => {});
          return win.focus();
        }
      }
      return self.clients.openWindow(target.href);
    }),
  );
});
