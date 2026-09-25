import type { InfiniteData } from "@tanstack/react-query";
import { describe, expect, it } from "vitest";
import { normalizeHandle } from "./characters";
import { applyLike } from "./likes";
import {
  isUuid,
  mapPostInPages,
  toPost,
  toPosts,
  type Post,
  type PostPage,
  type PostRowWithRelations,
} from "./posts";

const author = { id: "c1", handle: "misaki_ol", name: "美咲", avatar_url: "https://x/a.png" };

function row(id: string, overrides: Partial<PostRowWithRelations> = {}): PostRowWithRelations {
  return {
    id,
    character_id: "c1",
    image_url: `https://x/${id}.jpg`,
    caption: "caption",
    is_paid: false,
    price_tokens: 0,
    like_count: 3,
    comment_count: 2,
    published_at: "2026-09-25T00:00:00+00:00",
    character: author,
    my_likes: [],
    ...overrides,
  };
}

describe("toPost", () => {
  it("自分のいいね（RLS で本人の行のみ）があれば liked = true", () => {
    expect(toPost(row("p", { my_likes: [{ user_id: "me" }] }))?.liked).toBe(true);
    expect(toPost(row("p", { my_likes: [] }))?.liked).toBe(false);
    expect(toPost(row("p", { my_likes: null }))?.liked).toBe(false);
  });

  it("キャラが見えない行は除外する", () => {
    expect(toPost(row("p", { character: null }))).toBeNull();
    expect(toPosts([row("a"), row("b", { character: null }), row("c")]).map((p) => p.id)).toEqual([
      "a",
      "c",
    ]);
  });
});

describe("applyLike", () => {
  const post = toPost(row("p")) as Post;

  it("いいねで +1、取り消しで -1", () => {
    const liked = applyLike(post, true);
    expect(liked).toMatchObject({ liked: true, like_count: 4 });
    expect(applyLike(liked, false)).toMatchObject({ liked: false, like_count: 3 });
  });

  it("同じ状態なら同じ参照（冪等）", () => {
    expect(applyLike(post, false)).toBe(post);
  });

  it("0 未満にはならない", () => {
    expect(applyLike({ ...post, liked: true, like_count: 0 }, false).like_count).toBe(0);
  });
});

describe("mapPostInPages", () => {
  const data: InfiniteData<PostPage> = {
    pages: [
      { posts: toPosts([row("a"), row("b")]), nextCursor: { publishedAt: "t", id: "b" } },
      { posts: toPosts([row("c")]), nextCursor: null },
    ],
    pageParams: [null, { publishedAt: "t", id: "b" }],
  };

  it("該当する投稿だけ置き換え、他のページは同じ参照のまま", () => {
    const next = mapPostInPages(data, "c", (p) => ({ ...p, like_count: 99 }));
    expect(next.pages[1]?.posts[0]?.like_count).toBe(99);
    expect(next.pages[0]).toBe(data.pages[0]);
  });

  it("該当が無ければ同じ参照を返す", () => {
    expect(mapPostInPages(data, "zzz", (p) => ({ ...p, like_count: 1 }))).toBe(data);
  });
});

describe("isUuid", () => {
  it("UUID 形式のみ true", () => {
    expect(isUuid("00000000-0000-4000-8001-000000000101")).toBe(true);
    expect(isUuid("not-a-uuid")).toBe(false);
    expect(isUuid("")).toBe(false);
  });
});

describe("normalizeHandle", () => {
  it("デコード・@ 除去・小文字化して形式を検証する", () => {
    expect(normalizeHandle("misaki_ol")).toBe("misaki_ol");
    expect(normalizeHandle("%40Misaki_OL")).toBe("misaki_ol");
    expect(normalizeHandle("shizuku.letter")).toBe("shizuku.letter");
  });

  it("形式が不正なら null", () => {
    expect(normalizeHandle("a")).toBeNull();
    expect(normalizeHandle("bad handle")).toBeNull();
    expect(normalizeHandle("%E0%A4%A")).toBeNull();
    expect(normalizeHandle("x".repeat(31))).toBeNull();
  });
});
