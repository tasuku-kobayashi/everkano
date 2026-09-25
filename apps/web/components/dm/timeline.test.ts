import { describe, expect, it } from "vitest";
import type { TimelineMessage } from "@/lib/queries/messages";
import {
  bubbleRadiusClass,
  buildTimelineRows,
  formatSeparatorLabel,
  holdCharacterReplies,
  isEmojiOnly,
  isUuid,
  type MessageRow,
} from "./timeline";

// 2026-09-25（金）12:00 JST
const NOW = new Date("2026-09-25T03:00:00Z");

function m(
  id: string,
  createdAt: string,
  senderType: "user" | "character",
  status: TimelineMessage["status"] = "sent",
): TimelineMessage {
  return { key: id, id, senderType, body: id, createdAt, status };
}

function describeRows(rows: ReturnType<typeof buildTimelineRows>): string[] {
  return rows.map((row) =>
    row.kind === "separator"
      ? `--${row.label}--`
      : `${row.message.id}${row.isFirstInGroup ? "[" : ""}${row.isLastInGroup ? "]" : ""}`,
  );
}

describe("formatSeparatorLabel", () => {
  it("今日・昨日・曜日・日付（Asia/Tokyo）", () => {
    expect(formatSeparatorLabel("2026-09-25T05:03:00Z", NOW)).toBe("今日 14:03");
    expect(formatSeparatorLabel("2026-09-24T15:00:00Z", NOW)).toBe("今日 0:00"); // JST 0:00
    expect(formatSeparatorLabel("2026-09-24T14:59:00Z", NOW)).toBe("昨日 23:59");
    expect(formatSeparatorLabel("2026-09-22T01:05:00Z", NOW)).toBe("火曜日 10:05");
    expect(formatSeparatorLabel("2026-09-18T01:05:00Z", NOW)).toBe("9月18日 10:05");
    expect(formatSeparatorLabel("2025-12-31T01:05:00Z", NOW)).toBe("2025年12月31日 10:05");
  });

  it("不正な日時は空文字", () => {
    expect(formatSeparatorLabel("nope", NOW)).toBe("");
  });
});

describe("buildTimelineRows", () => {
  it("送り手ごとにグループ化し、先頭と 15 分以上の間隔に時刻の区切りを入れる", () => {
    const rows = buildTimelineRows(
      [
        m("c1", "2026-09-25T01:00:00Z", "character"),
        m("c2", "2026-09-25T01:00:30Z", "character"),
        m("u1", "2026-09-25T01:01:00Z", "user"),
        m("u2", "2026-09-25T01:02:00Z", "user"),
        m("u3", "2026-09-25T01:03:00Z", "user"),
        // 15 分以上空く → 区切り。送り手が同じでも別グループ
        m("u4", "2026-09-25T01:18:00Z", "user"),
        m("c3", "2026-09-25T01:18:01Z", "character"),
      ],
      NOW,
    );
    expect(describeRows(rows)).toEqual([
      "--今日 10:00--",
      "c1[",
      "c2]",
      "u1[",
      "u2",
      "u3]",
      "--今日 10:18--",
      "u4[]",
      "c3[]",
    ]);
  });

  it("15 分未満なら区切りを入れない（ちょうど 15 分は入れる）", () => {
    const rows = buildTimelineRows(
      [
        m("a", "2026-09-25T01:00:00Z", "user"),
        m("b", "2026-09-25T01:14:59Z", "user"),
        m("c", "2026-09-25T01:29:59Z", "user"),
      ],
      NOW,
    );
    expect(describeRows(rows)).toEqual(["--今日 10:00--", "a[", "b]", "--今日 10:29--", "c[]"]);
  });

  it("端末時刻が過去にずれたローカルメッセージでも区切り・順序が乱れない", () => {
    const rows = buildTimelineRows(
      [
        m("c1", "2026-09-25T02:00:00Z", "character"),
        // 端末の時計が 20 分遅れている送信中メッセージ
        m("local-1", "2026-09-25T01:40:00Z", "user", "sending"),
        m("local-2", "2026-09-25T01:40:05Z", "user", "failed"),
      ],
      NOW,
    );
    expect(describeRows(rows)).toEqual(["--今日 11:00--", "c1[]", "local-1[", "local-2]"]);
  });

  it("空なら空", () => {
    expect(buildTimelineRows([], NOW)).toEqual([]);
  });

  it("行の key はメッセージの key、区切りは sep- 付き", () => {
    const rows = buildTimelineRows([m("x", "2026-09-25T01:00:00Z", "user")], NOW);
    expect(rows.map((r) => r.key)).toEqual(["sep-x", "x"]);
    expect((rows[1] as MessageRow).message.id).toBe("x");
  });
});

describe("bubbleRadiusClass", () => {
  it("グループ内の位置で送り手側の角を詰める", () => {
    expect(bubbleRadiusClass("user", true, true)).not.toContain("4px");
    expect(bubbleRadiusClass("user", true, false)).toContain("rounded-br-[4px]");
    expect(bubbleRadiusClass("user", false, false)).toContain("rounded-tr-[4px] rounded-br-[4px]");
    expect(bubbleRadiusClass("character", false, true)).toContain("rounded-tl-[4px]");
    expect(bubbleRadiusClass("character", false, true)).toContain("rounded-bl-[22px]");
  });
});

describe("isEmojiOnly", () => {
  it("絵文字 1〜3 個だけなら true", () => {
    expect(isEmojiOnly("😊")).toBe(true);
    expect(isEmojiOnly(" ❤️ ")).toBe(true);
    expect(isEmojiOnly("👍🏻👍🏻")).toBe(true);
    expect(isEmojiOnly("👨‍👩‍👧")).toBe(true);
  });

  it("文字を含む・4 個以上・空は false", () => {
    expect(isEmojiOnly("ありがとう😊")).toBe(false);
    expect(isEmojiOnly("😊😊😊😊")).toBe(false);
    expect(isEmojiOnly("")).toBe(false);
    expect(isEmojiOnly("123")).toBe(false);
  });
});

describe("holdCharacterReplies", () => {
  const items = [
    m("c1", "2026-09-25T01:00:00Z", "character"),
    m("u1", "2026-09-25T01:01:00.000001Z", "user"),
    m("c2", "2026-09-25T01:01:00.000002Z", "character"),
  ];

  it("送信時点より新しいキャラ発言だけを隠す", () => {
    expect(holdCharacterReplies(items, "2026-09-25T01:00:00Z").map((i) => i.id)).toEqual([
      "c1",
      "u1",
    ]);
    expect(holdCharacterReplies(items, "2026-09-25T01:01:00.000002Z")).toHaveLength(3);
  });

  it("holdAfter = null ならキャラ発言をすべて待たせる", () => {
    expect(holdCharacterReplies(items, null).map((i) => i.id)).toEqual(["u1"]);
  });
});

describe("isUuid", () => {
  it("UUID 形式のみ true", () => {
    expect(isUuid("00000000-0000-4000-8000-000000000c10")).toBe(true);
    expect(isUuid("not-a-uuid")).toBe(false);
    expect(isUuid("")).toBe(false);
  });
});
