import { describe, expect, it } from "vitest";
import {
  anonymousUserName,
  formatChatTimestamp,
  formatCount,
  formatLikeCount,
  formatRelativeTime,
  formatRelativeTimeShort,
} from "./format";

// 2026-09-25 12:00 JST
const NOW = new Date("2026-09-25T03:00:00Z");
const ago = (ms: number) => new Date(NOW.getTime() - ms);
const MIN = 60_000;
const HOUR = 60 * MIN;
const DAY = 24 * HOUR;

describe("formatRelativeTime", () => {
  it("1分未満は「たった今」", () => {
    expect(formatRelativeTime(ago(0), NOW)).toBe("たった今");
    expect(formatRelativeTime(ago(59_000), NOW)).toBe("たった今");
    // 端末時計のずれで未来になっても「たった今」
    expect(formatRelativeTime(new Date(NOW.getTime() + 5 * MIN), NOW)).toBe("たった今");
  });

  it("分・時間・日", () => {
    expect(formatRelativeTime(ago(5 * MIN), NOW)).toBe("5分前");
    expect(formatRelativeTime(ago(59 * MIN), NOW)).toBe("59分前");
    expect(formatRelativeTime(ago(3 * HOUR), NOW)).toBe("3時間前");
    expect(formatRelativeTime(ago(23 * HOUR + 59 * MIN), NOW)).toBe("23時間前");
    expect(formatRelativeTime(ago(2 * DAY), NOW)).toBe("2日前");
    expect(formatRelativeTime(ago(6 * DAY + 23 * HOUR), NOW)).toBe("6日前");
  });

  it("7日以上前は日付（JST）", () => {
    expect(formatRelativeTime("2026-09-03T01:00:00Z", NOW)).toBe("9月3日");
    // UTC では 9/2 だが JST では 9/3
    expect(formatRelativeTime("2026-09-02T16:30:00Z", NOW)).toBe("9月3日");
    expect(formatRelativeTime("2025-12-31T00:00:00Z", NOW)).toBe("2025年12月31日");
  });

  it("文字列・数値入力と不正値", () => {
    expect(formatRelativeTime(NOW.getTime() - 10 * MIN, NOW)).toBe("10分前");
    expect(formatRelativeTime("2026-09-25T02:00:00Z", NOW)).toBe("1時間前");
    expect(formatRelativeTime("invalid", NOW)).toBe("");
  });
});

describe("formatRelativeTimeShort", () => {
  it("Instagram DM 形式", () => {
    expect(formatRelativeTimeShort(ago(10_000), NOW)).toBe("今");
    expect(formatRelativeTimeShort(ago(5 * MIN), NOW)).toBe("5分");
    expect(formatRelativeTimeShort(ago(3 * HOUR), NOW)).toBe("3時間");
    expect(formatRelativeTimeShort(ago(2 * DAY), NOW)).toBe("2日");
    expect(formatRelativeTimeShort(ago(15 * DAY), NOW)).toBe("2週間");
    expect(formatRelativeTimeShort(ago(400 * DAY), NOW)).toBe("2025年8月21日");
  });
});

describe("formatChatTimestamp", () => {
  it("今日・昨日・今年・過去年", () => {
    expect(formatChatTimestamp("2026-09-25T00:05:00Z", NOW)).toBe("9:05");
    expect(formatChatTimestamp("2026-09-24T05:30:00Z", NOW)).toBe("昨日 14:30");
    expect(formatChatTimestamp("2026-09-03T05:30:00Z", NOW)).toBe("9月3日 14:30");
    expect(formatChatTimestamp("2025-01-02T00:00:00Z", NOW)).toBe("2025年1月2日 9:00");
  });

  it("JST の日付境界で判定する", () => {
    // 2026-09-24T15:30Z = 9/25 0:30 JST（今日）
    expect(formatChatTimestamp("2026-09-24T15:30:00Z", NOW)).toBe("0:30");
    // 2026-09-24T14:59Z = 9/24 23:59 JST（昨日）
    expect(formatChatTimestamp("2026-09-24T14:59:00Z", NOW)).toBe("昨日 23:59");
  });
});

describe("formatCount", () => {
  it("1万未満はカンマ区切り", () => {
    expect(formatCount(0)).toBe("0");
    expect(formatCount(7)).toBe("7");
    expect(formatCount(1234)).toBe("1,234");
    expect(formatCount(9999)).toBe("9,999");
  });

  it("万・億（切り捨て）", () => {
    expect(formatCount(10_000)).toBe("1万");
    expect(formatCount(12_345)).toBe("1.2万");
    expect(formatCount(12_999)).toBe("1.2万");
    expect(formatCount(99_999)).toBe("9.9万");
    expect(formatCount(120_000)).toBe("12万");
    expect(formatCount(1_234_567)).toBe("123万");
    expect(formatCount(12_345_678)).toBe("1,234万");
    expect(formatCount(123_456_789)).toBe("1.2億");
    expect(formatCount(1_234_567_890)).toBe("12億");
  });

  it("不正値は 0 扱い", () => {
    expect(formatCount(-5)).toBe("0");
    expect(formatCount(Number.NaN)).toBe("0");
    expect(formatCount(3.7)).toBe("3");
  });

  it("formatLikeCount", () => {
    expect(formatLikeCount(1234)).toBe("「いいね！」1,234件");
  });
});

describe("anonymousUserName", () => {
  it("user_ + 先頭6桁", () => {
    expect(anonymousUserName("A1B2C3D4-0000-4000-8000-000000000000")).toBe("user_a1b2c3");
    expect(anonymousUserName("12-34-56-78")).toBe("user_123456");
  });
});
