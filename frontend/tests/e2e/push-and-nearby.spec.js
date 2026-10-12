// V-01(プッシュ登録の往復・05 v0.9)とP-04(nearby設定導線・03 v0.6)のE2E。
// - 登録往復: 通知許可grantedの文脈で設定画面を開く→SW登録+stubトークン→
//   POST /v1/users/me/push-token→UIの識別子表示+push_tokens行(DB証明)。
//   実FCM資材(VAPID鍵)は人間領域のためstubトークンで登録経路を証明する
//   (送信の実機確認はws-9)。
// - 許可がない場合(D-18): 登録は行われず・アプリ内通知(お知らせ)に到達する。
// - nearby: 設定トグルON→以後の新規Intent保存時notification_level=nearby_also
//   (DB証明)・nearby_candidate行の受信文言(G-12拡張)。
import { test, expect } from "./helpers.js";
import { api, cleanupAll, futureTimeText, pairLatchFixture, sql } from "./helpers.js";

test.afterAll(cleanupAll);

test("通知許可grantedでプッシュ登録が往復する(V-01・登録経路)", async ({ page, user }) => {
  // headless ChromiumはNotification.permissionが常に"denied"を返す既知の挙動
  // (permissions.queryはgrantedになるが同期getterは書き換わらない)。許可状態
  // のみこの注入で与え、SW登録・トークン生成・API往復・UI/DBは実経路のまま証明する。
  await page.addInitScript(() => {
    try {
      Object.defineProperty(Notification, "permission", { value: "granted" });
    } catch {
      /* 定義済みならそのまま */
    }
  });
  const posted = page.waitForRequest(
    (req) => req.method() === "POST" && req.url().endsWith("/v1/users/me/push-token"),
  );
  await page.goto("/#/settings");
  const req = await posted;
  const body = req.postDataJSON();
  expect(body.token).toMatch(/^stub-[0-9a-f]{16}$/); // 資材なし環境の疑似トークン(05 v0.9)
  expect(body.platform).toBe("web");
  // UI表示: last4マスクつき登録済み文言
  await expect(page.locator("[data-role=push-registration-status]")).toHaveText(
    /この端末で通知を受け取ります\(識別子 …[0-9a-f]{4}\)/,
    { timeout: 15_000 },
  );
  // DB証明: push_tokens行(token全文はUIに出ない)
  await expect
    .poll(
      () =>
        Number(
          sql(
            `SELECT count(*) FROM push_tokens WHERE user_id='${user.userId}'::uuid AND token='${body.token}';`,
          ),
        ),
      { timeout: 10_000 },
    )
    .toBe(1);
  // GET一覧もlast4のみ
  const list = await api("GET", "/v1/users/me/push-token", { token: user.access });
  expect(list.status).toBe(200);
  expect(list.data.tokens).toHaveLength(1);
  expect(list.data.tokens[0].token_last4).toBe(body.token.slice(-4));
  expect(list.data.tokens[0].token).toBeUndefined();
});

test("通知許可がない場合は登録せず・アプリ内通知へ到達する(V-01・D-18)", async ({ page, user }) => {
  // 既定contextは許可を与えていない(headless既定=denied)→登録しない
  await page.goto("/#/settings");
  await expect(page.locator("[data-role=push-registration-status]")).toHaveText(
    /通知の許可がないため、プッシュ通知の登録はありません。お知らせはアプリ内で届きます。/,
    { timeout: 15_000 },
  );
  expect(
    Number(sql(`SELECT count(*) FROM push_tokens WHERE user_id='${user.userId}'::uuid;`)),
  ).toBe(0);
  // アプリ内通知(D-18フォールバック): proposal行がお知らせpopoverへ届く
  const other = await pairLatchFixture({ tag: "v1push" });
  sql(
    `INSERT INTO notifications (id, user_id, type, payload, created_at) VALUES (gen_random_uuid(), '${user.userId}'::uuid, 'proposal', '{"latch_id": "${other.latchId}"}'::jsonb, now());`,
  );
  await page.getByRole("button", { name: "お知らせ" }).click();
  await expect(page.getByText("LATCH候補があります。").first()).toBeVisible({ timeout: 15_000 });
  // 登録していないことも再確認(通知受信で登録は生えない)
  expect(
    Number(sql(`SELECT count(*) FROM push_tokens WHERE user_id='${user.userId}'::uuid;`)),
  ).toBe(0);
});

test("nearbyトグルONで以後の新規Intentがnearby_alsoで保存される(P-04)", async ({ page, user }) => {
  await page.goto("/#/settings");
  const toggle = page.locator("[data-role=nearby-toggle]");
  await expect(toggle).not.toBeChecked();
  // 説明文は03 v0.6の文言(内容や相手は表示されません)
  await expect(page.getByText("近い条件の候補があれば知らせる")).toBeVisible();
  await expect(page.getByText(/内容や相手は表示されません。/)).toBeVisible();
  await toggle.click();
  await expect(toggle).toBeChecked();

  // 主幹フローで新規Intentを保存(02で時間・場所を編集して預ける)
  await page.goto("/#/");
  await page.locator("#intentText").fill("天文館で今夜19時から2人で飲み");
  await expect(page.locator("#readChips .chip").first()).toBeVisible({ timeout: 15_000 });
  await page.locator("#readButton").click();
  await expect(page.locator("#confirmScreen")).toBeVisible();
  await page.getByRole("button", { name: "時間を編集" }).click();
  await page.locator("#edit-time").fill(futureTimeText(2));
  await page.getByRole("button", { name: "保存" }).click();
  await page.getByRole("button", { name: "場所を編集" }).click();
  await page.locator("#edit-location").fill("天文館");
  await page.getByRole("button", { name: "保存" }).click();
  await page.getByText("含む", { exact: true }).click();
  await page.locator("#expiry").selectOption({ index: 2 });
  await page.locator("#depositButton").click();
  await expect(page).toHaveURL(/#\/standby/, { timeout: 15_000 });

  // DB証明: 保存時の値がnearby_also(トグルは端末保存・API値域は既存)
  const level = sql(
    `SELECT notification_level FROM intents WHERE user_id='${user.userId}'::uuid ORDER BY created_at DESC LIMIT 1;`,
  );
  expect(level).toBe("nearby_also");
});

test("nearby_candidate行は「近い条件の候補があるようです。」でリンクなし受信(P-04・G-12拡張)", async ({ page, user }) => {
  sql(
    `INSERT INTO notifications (id, user_id, type, payload, created_at) VALUES (gen_random_uuid(), '${user.userId}'::uuid, 'nearby_candidate', '{}'::jsonb, now());`,
  );
  await page.goto("/");
  await page.getByRole("button", { name: "お知らせ" }).click();
  const row = page.getByText("近い条件の候補があるようです。");
  await expect(row).toBeVisible({ timeout: 15_000 });
  // nearby行はリンクなし(div・条件サマリ・相手情報なし=03 §2/08 §2.2)
  await expect(row.locator("xpath=ancestor::a")).toHaveCount(0);
});
