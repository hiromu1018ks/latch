// 02「あなたの言葉」の原文ハイライト(D-03決定: クライアントの値マッチング)。
// 純関数 — 原文(rawText)から各条件行に対応する部分文字列を探し、
// {text, no} のセグメント列へ組む(マッチ部分は no=行番号"01"〜"05"を持つ)。
// マッチしない行はハイライトなし(画面は破綻しない — phase0-decisions D-03)。
// 数値・地名など核となる語で部分一致させる。ユーザー入力は一切HTMLへ
// 展開しない(セグメントはtextContentとして消費される)。
import { jstParts } from "./format.js";

const DAY_WORD_BY_DIFF = [
  ["今日", 0],
  ["今夜", 0],
  ["明日", 1],
  ["明後日", 2],
];

// 行キー→02の行番号(conditions.jsのROW_DEFSと同一順)
const ROW_NO = {
  time: "01",
  location: "02",
  participants: "03",
  budget: "04",
  category: "05",
};

// 条件行 → 原文中の候補位置(start/end)の列。行キーごとに探索方法が違う
const findCandidate = (key, structured, rawText, nowIso) => {
  const s = structured ?? {};
  switch (key) {
    case "time": {
      if (!s.time?.start) return null;
      const p = jstParts(s.time.start);
      const hour = String(Number(p.hour));
      const minute = String(Number(p.minute));
      const timeAlts =
        minute === "30"
          ? [`${hour}時半`, `${hour}:${p.minute}`]
          : minute === "0"
            ? [`${hour}時`, `${hour}:00`, `${hour}:${p.minute}`]
            : [`${hour}:${p.minute}`];
      // 日付差が一致する「今日/明日」等の語との隣接表記を先に試す(長い方を優先)
      const alts = [];
      if (nowIso) {
        const now = jstParts(nowIso);
        const ua = Date.UTC(now.year, now.month - 1, now.day);
        const ub = Date.UTC(p.year, p.month - 1, p.day);
        const diff = Math.round((ub - ua) / 86_400_000);
        for (const [word, d] of DAY_WORD_BY_DIFF) {
          if (d === diff) {
            for (const time of timeAlts) alts.push(`${word}${time}`, `${word} ${time}`);
          }
        }
      }
      alts.push(...timeAlts);
      for (const alt of alts) {
        const idx = rawText.indexOf(alt);
        if (idx >= 0) {
          // 後続の「以降/から/まで/頃」まで含めてマークする(プロトタイプ02の
          // マーク範囲「今日20時以降」に合わせる)
          const rest = /^(以降|から|まで|頃)/.exec(rawText.slice(idx + alt.length));
          const extra = rest ? rest[0].length : 0;
          return { start: idx, end: idx + alt.length + extra };
        }
      }
      return null;
    }
    case "location": {
      const name = s.location?.name;
      if (!name) return null;
      const idx = rawText.indexOf(name);
      return idx >= 0 ? { start: idx, end: idx + name.length } : null;
    }
    case "participants": {
      const min = s.participants?.min;
      const max = s.participants?.max;
      if (min == null && max == null) return null;
      const range = new RegExp(`${min}\\s*[〜~ー－-]\\s*${max}\\s*人`).exec(rawText);
      if (range) return { start: range.index, end: range.index + range[0].length };
      const single = new RegExp(`(${min ?? max})\\s*人`).exec(rawText);
      if (single) {
        return { start: single.index, end: single.index + single[0].length };
      }
      return null;
    }
    case "budget": {
      const max = s.budget?.max;
      if (max == null) return null;
      const alts = new Set([String(max), max.toLocaleString("ja-JP")]);
      if (max >= 10_000 && max % 10_000 === 0) {
        alts.add(`${max / 10_000}万`);
        alts.add(`${(max / 10_000).toLocaleString("ja-JP")}万`);
      }
      const money = /([\d,，.]+)\s*(万)?\s*円/.exec(rawText);
      if (money) {
        const value = Number(money[1].replace(/[，,]/g, "")) * (money[2] ? 10_000 : 1);
        if (alts.has(String(value)) || alts.has(value.toLocaleString("ja-JP"))) {
          // 前後の「予算」「まで」等の語まで含める(プロトタイプ02の
          // マーク範囲「予算5,000円まで」に合わせる)
          let start = money.index;
          let end = money.index + money[0].length;
          const prefix = /(予算|年収|月収|時給|希望額)$/.exec(rawText.slice(0, start));
          if (prefix) start -= prefix[0].length;
          const suffix = /^(まで|以下|以内|以上|から)/.exec(rawText.slice(end));
          if (suffix) end += suffix[0].length;
          return { start, end };
        }
      }
      return null;
    }
    case "category": {
      const value = s.category?.secondary ?? s.category?.primary;
      if (!value) return null;
      const idx = rawText.indexOf(value);
      if (idx >= 0) return { start: idx, end: idx + value.length };
      // 正規化差(「軽く飲む」↔「軽く飲みたい」)に備え、原文と一致する
      // 先頭共通接頭辞の最长ぶんまでマークする(最低2字)
      if (value.length >= 2) {
        const head = value.slice(0, 2);
        const hIdx = rawText.indexOf(head);
        if (hIdx >= 0) {
          let len = 2;
          while (len < value.length && rawText[hIdx + len] === value[len]) len += 1;
          return { start: hIdx, end: hIdx + len };
        }
      }
      return null;
    }
    default:
      return null;
  }
};

// セグメント組立。重なった候補は先頭位置が早い方・同位置なら長い方を残す
export const buildWordsSegments = (structured, rawText, nowIso) => {
  const text = String(rawText ?? "");
  const candidates = [];
  for (const [key, no] of Object.entries(ROW_NO)) {
    const hit = findCandidate(key, structured, text, nowIso);
    if (hit) candidates.push({ ...hit, no });
  }
  candidates.sort((a, b) => a.start - b.start || b.end - a.end);

  const taken = [];
  const segments = [];
  let cursor = 0;
  for (const hit of candidates) {
    if (hit.start < cursor) continue; // 重なりは棄却(先に確定した方を優先)
    if (hit.start > cursor) segments.push({ text: text.slice(cursor, hit.start) });
    segments.push({ text: text.slice(hit.start, hit.end), no: hit.no });
    taken.push(hit.no);
    cursor = hit.end;
  }
  if (cursor < text.length) segments.push({ text: text.slice(cursor) });
  return segments;
};
