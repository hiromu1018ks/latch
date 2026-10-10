// カレンダーに追加(D-10・クライアント完結)。成立した内容から.ics(RFC 5545)を
// 生成してダウンロードする。API・ネットワークは使わない(Blob+objectURL)。
// 開始はproposal.time_summary(JST「YYYY-MM-DD HH:MM」)・終端は開始+2時間の
// 固定値(終了時刻はAPIにないため)。日付はUTC(Z形)へ変換する。
import { CALENDAR_BUTTON_TEXT } from "./texts.js";
import { formatCategory } from "../intent/format.js";
import { escapeHtml } from "./view.js";

const EVENT_DURATION_MS = 2 * 60 * 60 * 1000;

const parseJstSummary = (timeSummary) => {
  // time_summaryは「YYYY-MM-DD HH:MM」(JST・proposal.pyの格納形式)
  const match = /^(\d{4})-(\d{2})-(\d{2}) (\d{2}):(\d{2})$/.exec(
    String(timeSummary ?? ""),
  );
  if (!match) return null;
  const [, y, m, d, hh, mm] = match;
  const date = new Date(`${y}-${m}-${d}T${hh}:${mm}:00+09:00`);
  return Number.isNaN(date.getTime()) ? null : date;
};

const utcStamp = (date) => {
  const pad = (n) => String(n).padStart(2, "0");
  return (
    `${date.getUTCFullYear()}${pad(date.getUTCMonth() + 1)}${pad(date.getUTCDate())}` +
    `T${pad(date.getUTCHours())}${pad(date.getUTCMinutes())}${pad(date.getUTCSeconds())}Z`
  );
};

// RFC 5545のTEXT値エスケープ(カンマ・セミコロン・改行)
const icsText = (value) =>
  String(value).replace(/\\/g, "\\\\").replace(/;/g, "\\;").replace(/,/g, "\\,").replace(/\r?\n/g, "\\n");

// .ics本文(テスト対象の純関数)。line foldingは省略(75オクテット規定は
// ローカル生成の短い行に影響しない)
export const buildIcs = ({ latch, nowIso = new Date().toISOString() }) => {
  const p = latch?.proposal ?? {};
  const start = parseJstSummary(latch?.time_summary ?? p.time_summary);
  if (!start) return null;
  const end = new Date(start.getTime() + EVENT_DURATION_MS);
  const category = formatCategory({
    primary: p.category_primary,
    secondary: p.category_secondary,
  });
  const lines = [
    "BEGIN:VCALENDAR",
    "VERSION:2.0",
    "PRODID:-//LATCH//Latched Event//JA",
    "CALSCALE:GREGORIAN",
    "BEGIN:VEVENT",
    `UID:${latch.id}@latch`,
    `DTSTAMP:${utcStamp(new Date(nowIso))}`,
    `DTSTART:${utcStamp(start)}`,
    `DTEND:${utcStamp(end)}`,
    `SUMMARY:${icsText(category === "—" ? "LATCH" : `LATCH（${category}）`)}`,
    ...(latch?.area_name ?? p.area_name
      ? [`LOCATION:${icsText(latch.area_name ?? p.area_name)}`]
      : []),
    "DESCRIPTION:LATCHが成立した予定です",
    "END:VEVENT",
    "END:VCALENDAR",
  ];
  return `${lines.join("\r\n")}\r\n`;
};

// 時刻が組めるか(カレンダーに追加ボタンの無効化判定。React版も同じ判定を使う)
export const hasCalendarTime = (latch) =>
  Boolean(parseJstSummary(latch?.time_summary ?? latch?.proposal?.time_summary));

// ボタンの組立helper(成立した内容カード内・時刻が取れない場合は無効化)
export const calendarButtonHtml = (latch) => {
  const p = latch?.proposal ?? {};
  const hasTime = Boolean(parseJstSummary(latch?.time_summary ?? p.time_summary));
  return `
    <button type="button" class="ghost lt-cal" data-role="calendar-add" ${
      hasTime ? "" : "disabled"
    }>${calendarIconHtml}${escapeHtml(CALENDAR_BUTTON_TEXT)}</button>`;
};

const calendarIconHtml =
  '<svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.7" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><rect x="4" y="5" width="16" height="15" rx="2"></rect><path d="M4 10h16M9 3v4M15 3v4"></path></svg>';

export const downloadIcs = (latch) => {
  const ics = buildIcs({ latch });
  if (!ics) return false;
  const blob = new Blob([ics], { type: "text/calendar;charset=utf-8" });
  const url = URL.createObjectURL(blob);
  const anchor = document.createElement("a");
  anchor.href = url;
  anchor.download = `latch-${latch.id}.ics`;
  document.body.append(anchor);
  anchor.click();
  anchor.remove();
  URL.revokeObjectURL(url);
  return true;
};
