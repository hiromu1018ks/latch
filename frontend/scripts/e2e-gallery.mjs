// E2E録画ギャラリー生成(people向け・file://で直接開ける純静的HTML)。
// 前提: `npx playwright test` がJSONレポーター(frontend/e2e-report/results.json)
// と動画(use.video="on" + 手動コンテキストのtestInfo.attach)を出力済みであること。
// 使い方: `npm run test:e2e:report`(playwright実行→本スクリプト)。
// 出力: frontend/e2e-recordings/<実行日時>/{videos/<機能群>/…, index.html}
// 過去runは最新3件まで自動削除する(動画はgit管理外・ローカル閲覧想定)。
import { cp, mkdir, readdir, readFile, rm, writeFile } from "node:fs/promises";
import { existsSync } from "node:fs";
import path from "node:path";
import { fileURLToPath } from "node:url";

const frontendRoot = path.resolve(path.dirname(fileURLToPath(import.meta.url)), "..");
const reportPath = path.join(frontendRoot, "e2e-report", "results.json");
const recordingsRoot = path.join(frontendRoot, "e2e-recordings");
const KEEP_RUNS = 3;

// specファイル → 機能群(接頭番号で並び順を固定)
const GROUPS = {
  "auth.spec.js": "01_認証・セッション",
  "consent.spec.js": "02_LLM同意ゲート",
  "main-flow.spec.js": "03_主幹フロー(預ける→確認→待機中)",
  "intent-lifecycle.spec.js": "04_取り下げ・編集",
  "response.spec.js": "05_候補への回答・undo",
  "chat.spec.js": "06_成立・チャット",
  "cross-user.spec.js": "07_横断アクセス(P0)",
  "errors-and-settings.spec.js": "08_エラー注入・設定",
  "misc.spec.js": "09_シェル・ビューポート",
};

// 主要テストの「何を見るか」(題名の部分一致で引く)。無ければ題名そのものが説明。
const HINTS = [
  ["undoするとopen姿", "YES回答→「参加と回答しました。」→「回答を取り消す」で提案の2択が復帰する瞬間(FG-07でunitに証明がなかった箇所)"],
  ["入力→読み取りchips", "入力から1秒でchipsが生えCTA有効化→02で時間・場所を行編集→「預ける」→03着地→リロード後も意思が残る"],
  ["関与しないユーザー", "第三者セッションでlatchのURLを直叩き→「提案が見つかりません」+ホーム導線(横断アクセス遮断)"],
  ["access切れ+refresh保持", "accessなし+refreshのみの再訪で自動refreshが走り画面が出る(findings#1修正の回帰)"],
  ["候補があると03着地", "右上の候補トーストとヘッダー/タブバッジ→「見る」で04へ遷移"],
  ["成立画面が開き", "動画1=自分・動画2=相手。自分の送信が相手側画面に反映される=サーバ永続の証明"],
  ["参加取り消し(leave)", "成立取り消しのdialog→再訪するとチャットが書き込み不可に収束する"],
  ["取り下げるとdialog確認", "dialog確認→取り下げ→リロードしても意思が戻らない(永続化)"],
  ["条件を直す", "03「条件を直す」→復元→人数を3人へ編集→保存→リロード後も変更維持"],
  ["お知らせpopover", "通知ボタン→行が既読化→リロード後も行が残る"],
  ["見送り+理由chips", "「今回は見送る」→理由chips→確定で待機中へ戻り、再訪しても見送り表示"],
  ["回答期限が過ぎた後", "回答後に期限を過ぎた状態を作り、undoが「取り消せませんでした」に収束"],
];

const hintFor = (title) => HINTS.find(([key]) => title.includes(key))?.[1] ?? "";
const slug = (s) => s.replace(/[\\/:*?"<>|\s]+/g, "_").slice(0, 80);
const esc = (s) =>
  String(s).replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" })[c]);

const collect = (suites, file, out) => {
  for (const suite of suites ?? []) {
    const suiteFile = suite.file ?? file;
    if (suite.suites) collect(suite.suites, suiteFile, out);
    for (const spec of suite.specs ?? []) {
      const result = spec.tests?.[0]?.results?.[0];
      if (!result) continue;
      const videos = (result.attachments ?? [])
        .filter((a) => a.contentType === "video/webm" && a.path)
        .map((a) => (path.isAbsolute(a.path) ? a.path : path.join(frontendRoot, a.path)));
      out.push({
        file: suiteFile,
        title: spec.title,
        status: result.status,
        duration: result.duration ?? 0,
        videos,
      });
    }
  }
};

const build = async () => {
  if (!existsSync(reportPath)) {
    throw new Error(`results.json がありません。先に npx playwright test を実行してください: ${reportPath}`);
  }
  const report = JSON.parse(await readFile(reportPath, "utf8"));
  const tests = [];
  collect(report.suites, null, tests);

  const stamp = new Date(report.stats?.startTime ?? Date.now());
  const pad = (n) => String(n).padStart(2, "0");
  const runId = `${stamp.getFullYear()}${pad(stamp.getMonth() + 1)}${pad(stamp.getDate())}-${pad(stamp.getHours())}${pad(stamp.getMinutes())}${pad(stamp.getSeconds())}`;
  const runDir = path.join(recordingsRoot, runId);
  await mkdir(runDir, { recursive: true });

  const passed = tests.filter((t) => t.status === "passed").length;
  const cards = [];
  let seq = 0;
  for (const t of tests) {
    const group = GROUPS[path.basename(t.file)] ?? "99_その他";
    const videos = t.videos.filter((v) => existsSync(v));
    const copied = [];
    for (let i = 0; i < videos.length; i++) {
      seq += 1;
      const rel = path.posix.join(
        "videos",
        slug(group),
        `${String(seq).padStart(2, "0")}-${slug(t.title)}${videos.length > 1 ? `-v${i + 1}` : ""}.webm`,
      );
      const dest = path.join(runDir, rel);
      await mkdir(path.dirname(dest), { recursive: true });
      await cp(videos[i], dest);
      copied.push(rel);
    }
    cards.push({ ...t, group, copied });
  }

  const byGroup = new Map();
  for (const c of cards) {
    if (!byGroup.has(c.group)) byGroup.set(c.group, []);
    byGroup.get(c.group).push(c);
  }

  const sections = [...byGroup.entries()]
    .sort(([a], [b]) => a.localeCompare(b))
    .map(([group, items]) => {
      const gid = slug(group);
      const cardsHtml = items
        .map((c) => {
          const badge =
            c.status === "passed" ? "pass" : c.status === "skipped" ? "skip" : "fail";
          const badgeText = { pass: "合格", skip: "スキップ", fail: "失敗" }[badge];
          const vids = c.copied
            .map(
              (rel, i) =>
                `<video controls preload="metadata" src="${esc(rel)}"${c.copied.length > 1 ? ` title="動画${i + 1}"` : ""}></video>`,
            )
            .join("\n          ");
          return `
        <article class="card ${badge}">
          <header>
            <span class="badge ${badge}">${badgeText}</span>
            <h3>${esc(c.title)}</h3>
            <span class="meta">${(c.duration / 1000).toFixed(1)}秒・${esc(path.basename(c.file))}</span>
          </header>
          ${c.copied.length > 1 ? `<p class="who">動画は上から 操作する主体の順(自分→相手)</p>` : ""}
          ${vids || `<p class="novideo">(このテストには動画がありません)</p>`}
          ${hintFor(c.title) ? `<p class="hint">注目点: ${esc(hintFor(c.title))}</p>` : ""}
        </article>`;
        })
        .join("\n");
      return `
    <section id="${gid}">
      <h2>${esc(group)} <small>(${items.length}件)</small></h2>
${cardsHtml}
    </section>`;
    })
    .join("\n");

  const toc = [...byGroup.keys()]
    .sort((a, b) => a.localeCompare(b))
    .map((g) => `<a href="#${slug(g)}">${esc(g)}</a>`)
    .join("\n      ");

  const html = `<!doctype html>
<html lang="ja">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>LATCH E2E録画ギャラリー ${runId}</title>
<style>
  :root { color-scheme: light; }
  body { font-family: "Zen Kaku Gothic New", system-ui, sans-serif; margin: 0; background: #f4f2ec; color: #1d232b; }
  header.top { padding: 24px 32px 8px; }
  header.top h1 { font-size: 20px; margin: 0 0 4px; }
  header.top p { margin: 2px 0; color: #55524a; font-size: 13px; }
  nav.toc { padding: 8px 32px 16px; display: flex; flex-wrap: wrap; gap: 8px; }
  nav.toc a { text-decoration: none; background: #fff; border: 1px solid #d8d2c4; border-radius: 999px; padding: 4px 12px; font-size: 13px; }
  main { padding: 0 32px 48px; max-width: 1180px; }
  section h2 { font-size: 17px; border-left: 4px solid #0e7a5f; padding-left: 10px; margin: 28px 0 12px; }
  section h2 small { color: #888; font-weight: normal; }
  .card { background: #fff; border: 1px solid #ddd6c8; border-radius: 10px; padding: 12px 14px; margin-bottom: 14px; }
  .card.fail { border-color: #c0392b; }
  .card header { display: flex; align-items: baseline; gap: 10px; flex-wrap: wrap; }
  .card h3 { font-size: 14px; margin: 0; flex: 1; }
  .meta { color: #888; font-size: 12px; }
  .badge { font-size: 11px; padding: 2px 8px; border-radius: 999px; }
  .badge.pass { background: #dff0e6; color: #0e7a5f; }
  .badge.fail { background: #f8ddda; color: #a03024; }
  .badge.skip { background: #eee; color: #777; }
  video { display: inline-block; width: 340px; max-width: 100%; margin: 10px 12px 0 0; border: 1px solid #ccc; border-radius: 6px; background: #111; }
  .hint { font-size: 12.5px; color: #55524a; background: #f6f1e4; border-radius: 6px; padding: 6px 10px; margin: 8px 0 0; }
  .who { font-size: 12px; color: #777; margin: 6px 0 0; }
  .novideo { color: #999; font-size: 13px; }
</style>
</head>
<body>
  <header class="top">
    <h1>LATCH E2E録画ギャラリー</h1>
    <p>実行: ${runId}・${report.stats?.startTime ? esc(new Date(report.stats.startTime).toLocaleString("ja-JP")) : ""}</p>
    <p><strong>${tests.length}</strong>テスト(合格 ${passed}・失敗 ${tests.filter((t) => t.status === "failed").length}・スキップ ${tests.filter((t) => t.status === "skipped").length})・全体 ${(report.stats?.duration / 1000 || 0).toFixed(1)}秒・動画 ${seq}本</p>
  </header>
  <nav class="toc">
      ${toc}
  </nav>
  <main>
${sections}
  </main>
</body>
</html>
`;
  await writeFile(path.join(runDir, "index.html"), html, "utf8");

  // 過去runの掃除(最新KEEP_RUNS件のみ残す)
  const runs = (await readdir(recordingsRoot)).filter((d) => /^\d{8}-\d{6}$/.test(d)).sort();
  for (const old of runs.slice(0, Math.max(0, runs.length - KEEP_RUNS))) {
    await rm(path.join(recordingsRoot, old), { recursive: true, force: true });
  }

  console.log(`ギャラリー生成: ${path.join(runDir, "index.html")} (動画${seq}本・テスト${tests.length}件)`);
};

build().catch((err) => {
  console.error(err);
  process.exit(1);
});
