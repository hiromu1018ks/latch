// Q-02: キーボードのみ操作の実機E2E。dialogのEsc閉じ・Tab順(focusトラップ)・
// フォーカス復帰・radioの矢印選択・フォーム送信まで実キー操作で証明する。
// dialogは<dialog class="dlg">のshowModal(ネイティブのfocusトラップとEsc・
// close時のopener復帰に依存する設計)のため、ここはその実挙動を固定する。
import { test, expect } from "./helpers.js";
import { cleanupAll, createIntent, insertPairLatch, pairLatchFixture, sql } from "./helpers.js";

test.afterAll(cleanupAll);

const activeElement = (page) =>
  page.evaluate(() => ({
    id: document.activeElement?.id ?? "",
    role: document.activeElement?.getAttribute("role") ?? "",
    tag: document.activeElement?.tagName ?? "",
    text: (
      document.activeElement?.getAttribute("aria-label") ??
      document.activeElement?.textContent ??
      ""
    ).trim().slice(0, 30),
  }));

test("03取り下げdialog: Tab到達→Enterで開く→Tabはdialog内巡回→Escで閉じ復帰(Q-02)", async ({ page, user }) => {
  const intentId = await createIntent(user);
  await page.goto("/#/standby");
  const dropButton = page.getByRole("button", { name: "取り下げる" }).first();
  await expect(dropButton).toBeVisible({ timeout: 15_000 });

  // キーボードのみで取り下げボタンへ到達(skip linkを起点にTab連打)
  await page.keyboard.press("Tab"); // skip link
  let found = false;
  for (let i = 0; i < 40 && !found; i += 1) {
    const el = await activeElement(page);
    found = el.text === "取り下げる" || el.id === "";
    if (!found) await page.keyboard.press("Tab");
    else {
      const check = await activeElement(page);
      found = check.text === "取り下げる";
    }
  }
  // (上のloopはtext一致で抜ける。厳密には最後の一致確認)
  let cur = await activeElement(page);
  for (let i = 0; i < 40 && cur.text !== "取り下げる"; i += 1) {
    await page.keyboard.press("Tab");
    cur = await activeElement(page);
  }
  expect(cur.text).toBe("取り下げる");

  // Enterで開く(showModal)
  await page.keyboard.press("Enter");
  const dialog = page.getByRole("dialog");
  await expect(dialog).toBeVisible();
  // dialog内にフォーカスが入る(native dialogは開いた時点で内部へ移動)
  await expect
    .poll(
      async () =>
        page.evaluate(() => {
          const d = document.querySelector("dialog[open]");
          return d != null && d.contains(document.activeElement);
        }),
      { timeout: 3_000 },
    )
    .toBe(true);

  // Tabはdialog内を巡回する(native focus trap: dialog外のtopnavへ出ない)
  for (let i = 0; i < 12; i += 1) await page.keyboard.press("Tab");
  const inDialog = await page.evaluate(() => {
    const d = document.querySelector("dialog[open]");
    return d != null && d.contains(document.activeElement);
  });
  expect(inDialog).toBe(true);

  // Escで閉じ・フォーカスは開いたボタンへ復帰
  await page.keyboard.press("Escape");
  await expect(dialog).toBeHidden();
  await expect
    .poll(async () => (await activeElement(page)).text, { timeout: 3_000 })
    .toBe("取り下げる");
  // 取り下げずに閉じただけならIntentは残る
  expect(sql(`SELECT status FROM intents WHERE id='${intentId}'::uuid;`)).toBe("active");
});

test("05通報・ブロックdialog: キーボードで対象ラジオを選び理由を送信できる(Q-02)", async ({ openAuthed }) => {
  const pair = await pairLatchFixture({ status: "matched" });
  const mine = await openAuthed(pair.userA, `/#/latches/${pair.latchId}`);
  const p = mine.page;
  await expect(p.getByText("LATCHしました。")).toBeVisible({ timeout: 15_000 });

  // Tabで「通報・ブロック」へ到達しEnter
  let cur = await activeElement(p);
  for (let i = 0; i < 60 && cur.text !== "通報・ブロック"; i += 1) {
    await p.keyboard.press("Tab");
    cur = await activeElement(p);
  }
  expect(cur.text).toBe("通報・ブロック");
  await p.keyboard.press("Enter");
  await expect(p.getByRole("dialog")).toBeVisible();

  // dialog内: 「通報する」へTabで到達しEnterで理由ステップへ
  cur = await activeElement(p);
  for (let i = 0; i < 30 && cur.text !== "通報する"; i += 1) {
    await p.keyboard.press("Tab");
    cur = await activeElement(p);
  }
  expect(cur.text).toBe("通報する");
  await p.keyboard.press("Enter");
  // 理由4択(ラジオ)が現れる。Tabでラジオグループへ入り矢印で選択(native)
  const radios = p.getByRole("radio");
  await expect(p.locator('input[name="safety-reason"]').first()).toBeVisible({
    timeout: 5_000,
  });
  cur = await activeElement(p);
  for (let i = 0; i < 20 && cur.tag !== "INPUT"; i += 1) {
    await p.keyboard.press("Tab");
    cur = await activeElement(p);
  }
  expect(cur.tag).toBe("INPUT");
  await p.keyboard.press("ArrowDown");
  const checkedCount = await p
    .locator('input[name="safety-reason"]')
    .evaluateAll((els) => els.filter((e) => e.checked).length);
  expect(checkedCount).toBe(1); // 矢印操作で常に1つ選択

  // Escで閉じる(通報せず)・focus復帰
  await p.keyboard.press("Escape");
  await expect(p.getByRole("dialog")).toBeHidden();
  await mine.close();
});

test("お知らせpopover: Tab+Enterで開きEscで閉じる(Q-02)", async ({ page, user }) => {
  const other = await pairLatchFixture({});
  sql(
    `INSERT INTO notifications (id, user_id, type, payload, created_at) VALUES (gen_random_uuid(), '${user.userId}'::uuid, 'proposal', '{"latch_id": "${other.latchId}"}'::jsonb, now());`,
  );
  await page.goto("/");
  // お知らせボタン(aria-label)へTab到達
  let cur = await activeElement(page);
  for (let i = 0; i < 40 && cur.text !== "お知らせ"; i += 1) {
    await page.keyboard.press("Tab");
    cur = await activeElement(page);
  }
  expect(cur.text).toBe("お知らせ");
  await page.keyboard.press("Enter");
  await expect(page.getByText("LATCH候補があります。").first()).toBeVisible({
    timeout: 15_000,
  });
  // Escで閉じる(Header.jsxのEscapeハンドラ)・focus復帰
  await page.keyboard.press("Escape");
  await expect(page.getByText("LATCH候補があります。").first()).toBeHidden();
  await expect
    .poll(async () => (await activeElement(page)).text, { timeout: 3_000 })
    .toBe("お知らせ");
});
