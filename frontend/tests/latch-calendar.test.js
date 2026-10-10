import { describe, expect, it, vi } from "vitest";
import { buildIcs, calendarButtonHtml } from "../src/latch/calendar.js";

const latch = {
  id: "11111111-1111-4111-8111-111111111111",
  status: "matched",
  time_summary: "2026-10-10 20:30",
  area_name: "天文館周辺",
  proposal: {
    time_summary: "2026-10-10 20:30",
    area_name: "天文館周辺",
    category_primary: "drinking",
    category_secondary: "軽く飲めるお店",
  },
};

describe("カレンダーに追加(D-10・クライアント完結の.ics)", () => {
  it("time_summary(JST)をUTCのDTSTART/DTEND(+2時間固定)へ変換する", () => {
    const ics = buildIcs({ latch, nowIso: "2026-10-10T12:00:00+09:00" });
    expect(ics).toContain("DTSTART:20261010T113000Z"); // 20:30 JST = 11:30 UTC
    expect(ics).toContain("DTEND:20261010T133000Z"); // +2時間(終了時刻はAPIにない)
    expect(ics).toContain("DTSTAMP:20261010T030000Z");
    expect(ics).toContain(`UID:${latch.id}@latch`);
  });

  it("SUMMARY=種別・LOCATION=場所を含む(RFC 5545のエスケープ)", () => {
    const ics = buildIcs({ latch, nowIso: "2026-10-10T12:00:00+09:00" });
    expect(ics).toContain("SUMMARY:LATCH（軽く飲めるお店）");
    expect(ics).toContain("LOCATION:天文館周辺");
    const comma = buildIcs({
      latch: { ...latch, area_name: "天文館, その周辺" },
      nowIso: "2026-10-10T12:00:00+09:00",
    });
    expect(comma).toContain("LOCATION:天文館\\, その周辺");
  });

  it("種別不明はSUMMARY=LATCH・time_summaryが組めない場合はnull(ボタン無効化)", () => {
    const bare = buildIcs({
      latch: {
        ...latch,
        proposal: { ...latch.proposal, category_primary: null, category_secondary: null },
      },
      nowIso: "2026-10-10T12:00:00+09:00",
    });
    expect(bare).toContain("SUMMARY:LATCH");
    expect(buildIcs({
      latch: { ...latch, time_summary: null, proposal: { ...latch.proposal, time_summary: null } },
    })).toBeNull();
    expect(calendarButtonHtml({
      ...latch,
      time_summary: null,
      proposal: { ...latch.proposal, time_summary: null },
    })).toContain("disabled");
    expect(calendarButtonHtml(latch)).not.toContain("disabled");
  });
});
