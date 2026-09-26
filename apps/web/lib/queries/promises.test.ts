import type { PromiseDTO } from "@everkano/shared";
import { describe, expect, it } from "vitest";
import { formatPromiseDue, sortPromises } from "./promises";

// 2026-09-26（土）10:00 JST
const NOW = new Date("2026-09-26T01:00:00Z");

/** JST の日時（"2026-09-27 12:00"）→ ISO（UTC） */
function jst(value: string): string {
  return new Date(`${value.replace(" ", "T")}:00+09:00`).toISOString();
}

function promise(
  id: string,
  dueAt: string | null,
  precision: PromiseDTO["due_precision"] = "day",
  createdAt = "2026-09-20T00:00:00Z",
): PromiseDTO {
  return {
    id,
    character_id: "c",
    content: `約束 ${id}`,
    due_at: dueAt,
    due_precision: precision,
    status: "pending",
    created_at: createdAt,
    updated_at: createdAt,
  };
}

describe("formatPromiseDue（日本時間の相対表現）", () => {
  it("日付（day）: 今日・明日・あさって・N日後・昨日・N日前・それ以外は日付", () => {
    const due = (value: string) => formatPromiseDue(promise("p", jst(value)), NOW);
    expect(due("2026-09-26 12:00")).toEqual({ label: "今日", date: null, overdue: false });
    expect(due("2026-09-27 12:00")).toEqual({ label: "明日", date: null, overdue: false });
    expect(due("2026-09-28 12:00")).toEqual({
      label: "あさって",
      date: "9月28日（月）",
      overdue: false,
    });
    expect(due("2026-10-01 12:00")).toEqual({
      label: "5日後",
      date: "10月1日（木）",
      overdue: false,
    });
    expect(due("2026-10-03 12:00")).toEqual({
      label: "10月3日（土）",
      date: null,
      overdue: false,
    });
    expect(due("2026-09-25 12:00")).toEqual({ label: "昨日", date: null, overdue: true });
    expect(due("2026-09-22 12:00")).toMatchObject({ label: "4日前", overdue: true });
    expect(due("2027-01-05 12:00").label).toBe("2027年1月5日（火）");
  });

  it("日本時間の暦日で判定する（UTC では前日でも JST で今日なら「今日」）", () => {
    // 2026-09-26 00:30 JST = 2026-09-25 15:30 UTC
    expect(formatPromiseDue(promise("p", "2026-09-25T15:30:00Z"), NOW).label).toBe("今日");
  });

  it("日時（datetime）: 時刻を添え、過ぎていれば overdue", () => {
    const due = (value: string) => formatPromiseDue(promise("p", jst(value), "datetime"), NOW);
    expect(due("2026-09-26 15:00")).toEqual({ label: "今日 15:00", date: null, overdue: false });
    expect(due("2026-09-26 09:30")).toMatchObject({ label: "今日 9:30", overdue: true });
    expect(due("2026-09-27 09:05").label).toBe("明日 9:05");
    expect(due("2026-10-02 19:00").label).toBe("10月2日（金） 19:00");
  });

  it("週（week）: 今週・来週・先週・それ以外は「M月D日の週」（月曜はじまり）", () => {
    const due = (value: string) => formatPromiseDue(promise("p", jst(value), "week"), NOW);
    expect(due("2026-09-24 12:00")).toEqual({ label: "今週", date: null, overdue: false });
    expect(due("2026-10-01 12:00")).toEqual({ label: "来週", date: null, overdue: false });
    expect(due("2026-09-17 12:00")).toEqual({ label: "先週", date: null, overdue: true });
    expect(due("2026-10-08 12:00").label).toBe("10月5日の週");
  });

  it("月（month）: 今月・来月・先月・それ以外は月", () => {
    const due = (value: string) => formatPromiseDue(promise("p", jst(value), "month"), NOW);
    expect(due("2026-09-15 12:00")).toMatchObject({ label: "今月", overdue: false });
    expect(due("2026-10-15 12:00")).toMatchObject({ label: "来月", overdue: false });
    expect(due("2026-08-15 12:00")).toMatchObject({ label: "先月", overdue: true });
    expect(due("2026-12-15 12:00").label).toBe("12月");
    expect(due("2027-02-15 12:00").label).toBe("2027年2月");
  });

  it("期日なし・unknown は「日付未定」", () => {
    expect(formatPromiseDue(promise("p", null, "unknown"), NOW)).toEqual({
      label: "日付未定",
      date: null,
      overdue: false,
    });
    expect(formatPromiseDue(promise("p", jst("2026-09-27 12:00"), "unknown"), NOW).label).toBe(
      "日付未定",
    );
  });
});

describe("sortPromises", () => {
  it("これから（期日の近い順）→ 日付未定 → 期日を過ぎたもの（新しい順）", () => {
    const list = [
      promise("past-old", jst("2026-09-20 12:00")),
      promise("undated", null, "unknown"),
      promise("next-week", jst("2026-10-01 12:00")),
      promise("past-new", jst("2026-09-25 12:00")),
      promise("tomorrow", jst("2026-09-27 12:00")),
      promise("today", jst("2026-09-26 12:00")),
    ];
    expect(sortPromises(list, NOW).map((p) => p.id)).toEqual([
      "today",
      "tomorrow",
      "next-week",
      "undated",
      "past-new",
      "past-old",
    ]);
  });
});
