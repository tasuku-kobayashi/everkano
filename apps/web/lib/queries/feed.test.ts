import { describe, expect, it } from "vitest";
import {
  buildNextCursor,
  cursorOrFilter,
  FEED_PAGE_SIZE,
  quotePostgrestValue,
  STORY_RECENT_MS,
  toStoryItems,
} from "./feed";

const row = (id: string, published_at: string) => ({ id, published_at });

describe("quotePostgrestValue", () => {
  it("ダブルクォートで囲む", () => {
    expect(quotePostgrestValue("2026-09-25T12:17:56.055631+00:00")).toBe(
      '"2026-09-25T12:17:56.055631+00:00"',
    );
  });

  it("バックスラッシュとダブルクォートをエスケープする", () => {
    expect(quotePostgrestValue('a"b\\c')).toBe('"a\\"b\\\\c"');
  });
});

describe("buildNextCursor", () => {
  it("要求件数に満たなければ最後のページ（null）", () => {
    expect(buildNextCursor([row("a", "2026-01-01T00:00:00+00:00")], 10)).toBeNull();
    expect(buildNextCursor([], FEED_PAGE_SIZE)).toBeNull();
  });

  it("要求件数ちょうどなら最後の行の (published_at, id) を返す", () => {
    const rows = [
      row("c", "2026-01-03T00:00:00.123456+00:00"),
      row("b", "2026-01-02T00:00:00+00:00"),
      row("a", "2026-01-01T00:00:00.000001+00:00"),
    ];
    expect(buildNextCursor(rows, 3)).toEqual({
      publishedAt: "2026-01-01T00:00:00.000001+00:00",
      id: "a",
    });
  });

  it("published_at は DB の文字列をそのまま保持する（マイクロ秒を丸めない）", () => {
    const cursor = buildNextCursor([row("x", "2026-09-25T12:17:56.055631+00:00")], 1);
    expect(cursor?.publishedAt).toBe("2026-09-25T12:17:56.055631+00:00");
  });
});

describe("cursorOrFilter", () => {
  it("(published_at, id) の辞書式で「より古い」行を取る or フィルター", () => {
    expect(
      cursorOrFilter({
        publishedAt: "2026-09-25T12:17:56.055631+00:00",
        id: "00000000-0000-4000-8001-000000000401",
      }),
    ).toBe(
      'published_at.lt."2026-09-25T12:17:56.055631+00:00",' +
        'and(published_at.eq."2026-09-25T12:17:56.055631+00:00",id.lt."00000000-0000-4000-8001-000000000401")',
    );
  });

  it("値に構文文字が含まれてもクォートで保護される", () => {
    const filter = cursorOrFilter({ publishedAt: 'x"),or(', id: "y" });
    expect(filter).toContain('published_at.lt."x\\"),or("');
  });
});

describe("toStoryItems", () => {
  const now = Date.parse("2026-09-25T12:00:00Z");
  const char = (handle: string, posts: { id: string; published_at: string }[] | null) => ({
    id: `id-${handle}`,
    handle,
    name: handle.toUpperCase(),
    avatar_url: `https://example.com/${handle}.png`,
    posts,
  });

  it("24 時間以内に投稿したキャラを先頭に、新しい順に並べる", () => {
    const items = toStoryItems(
      [
        char("old", [row("p1", "2026-09-20T00:00:00Z")]),
        char("none", []),
        char("recent2", [row("p2", "2026-09-25T01:00:00Z")]),
        char("recent1", [row("p3", "2026-09-25T11:00:00Z")]),
      ],
      now,
    );
    expect(items.map((item) => item.character.handle)).toEqual([
      "recent1",
      "recent2",
      "old",
      "none",
    ]);
    expect(items.map((item) => item.isRecent)).toEqual([true, true, false, false]);
  });

  it("投稿が無いキャラは latestPost = null", () => {
    const [item] = toStoryItems([char("none", null)], now);
    expect(item?.latestPost).toBeNull();
    expect(item?.isRecent).toBe(false);
  });

  it("ちょうど 24 時間前は「最近」に含めない", () => {
    const edge = new Date(now - STORY_RECENT_MS).toISOString();
    const [item] = toStoryItems([char("edge", [row("p", edge)])], now);
    expect(item?.isRecent).toBe(false);
  });
});
