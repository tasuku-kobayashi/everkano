import { describe, expect, it } from "vitest";
import { anonymousUserName } from "@/lib/format";
import type { TypedSupabaseClient } from "@/lib/supabase/types";
import {
  buildCommentThreads,
  commentAuthorLabel,
  commentFromDto,
  commentMentionLabel,
  COMMENTS_LIMIT,
  fetchComments,
  isOwnComment,
  mergeComments,
  removeComments,
  replyMentionFor,
  threadRootId,
  type PostComment,
} from "./comments";

const POST_ID = "00000000-0000-4000-8001-000000000101";
const ME = "a1b2c3d4-e5f6-4000-8000-000000000001";
const OTHER = "ffeedd11-2233-4000-8000-000000000002";
const MISAKI = { id: "c1", handle: "misaki_ol", name: "美咲", avatar_url: "https://x/a.png" };

let seq = 0;
function comment(id: string, opts: Partial<PostComment> & { minute?: number } = {}): PostComment {
  seq += 1;
  const minute = opts.minute ?? seq;
  return {
    id,
    post_id: POST_ID,
    parent_comment_id: null,
    author_type: "user",
    author_user_id: OTHER,
    author_character_id: null,
    body: `body ${id}`,
    created_at: new Date(Date.UTC(2026, 8, 25, 0, minute)).toISOString(),
    character: null,
    ...opts,
  };
}

describe("buildCommentThreads", () => {
  it("トップレベルを時系列昇順に並べ、返信をその下にまとめる", () => {
    const threads = buildCommentThreads([
      comment("b", { minute: 2 }),
      comment("a", { minute: 1 }),
      comment("a-r1", { minute: 3, parent_comment_id: "a" }),
      comment("c", { minute: 4 }),
      comment("b-r1", { minute: 5, parent_comment_id: "b" }),
      comment("a-r2", { minute: 6, parent_comment_id: "a" }),
    ]);
    expect(threads.map((t) => t.root.id)).toEqual(["a", "b", "c"]);
    expect(threads[0]?.replies.map((r) => r.id)).toEqual(["a-r1", "a-r2"]);
    expect(threads[1]?.replies.map((r) => r.id)).toEqual(["b-r1"]);
    expect(threads[2]?.replies).toEqual([]);
  });

  it("返信への返信はトップレベルのスレッドに 1 段でまとめる（Instagram 方式）", () => {
    const threads = buildCommentThreads([
      comment("root", { minute: 1 }),
      comment("r1", { minute: 2, parent_comment_id: "root" }),
      comment("r1-1", { minute: 3, parent_comment_id: "r1" }),
    ]);
    expect(threads).toHaveLength(1);
    expect(threads[0]?.replies.map((r) => r.id)).toEqual(["r1", "r1-1"]);
  });

  it("親が見つからない返信はトップレベルとして表示する", () => {
    const threads = buildCommentThreads([
      comment("orphan", { minute: 1, parent_comment_id: "deleted" }),
      comment("x", { minute: 2 }),
    ]);
    expect(threads.map((t) => t.root.id)).toEqual(["orphan", "x"]);
  });

  it("同時刻は id 順で安定して並ぶ", () => {
    const threads = buildCommentThreads([comment("b", { minute: 1 }), comment("a", { minute: 1 })]);
    expect(threads.map((t) => t.root.id)).toEqual(["a", "b"]);
  });
});

describe("threadRootId", () => {
  const root = comment("root", { minute: 1 });
  const reply = comment("r1", { minute: 2, parent_comment_id: "root" });
  const nested = comment("r1-1", { minute: 3, parent_comment_id: "r1" });
  const all = [root, reply, nested];

  it("祖先をたどってトップレベルの ID を返す（配列・Map のどちらでも同じ）", () => {
    expect(threadRootId(all, nested)).toBe("root");
    expect(threadRootId(new Map(all.map((c) => [c.id, c])), nested)).toBe("root");
    expect(threadRootId(all, root)).toBe("root");
  });

  it("親が見えない返信・循環はそこで打ち切る", () => {
    const orphan = comment("orphan", { parent_comment_id: "deleted" });
    expect(threadRootId(all, orphan)).toBe("orphan");
    const a = comment("a", { parent_comment_id: "b" });
    const b = comment("b", { parent_comment_id: "a" });
    expect(threadRootId([a, b], a)).toBe("b");
  });
});

describe("mergeComments", () => {
  it("id で重複排除し、追加分だけ added に入れる", () => {
    const a = comment("a", { minute: 1 });
    const b = comment("b", { minute: 2 });
    const first = mergeComments([a], [b]);
    expect(first.comments.map((c) => c.id)).toEqual(["a", "b"]);
    expect(first.added.map((c) => c.id)).toEqual(["b"]);

    // API のレスポンスと Realtime の INSERT が両方届いても 1 件
    const second = mergeComments(first.comments, [b]);
    expect(second.comments.map((c) => c.id)).toEqual(["a", "b"]);
    expect(second.added).toEqual([]);
  });

  it("時系列昇順に並べ直す", () => {
    const merged = mergeComments(
      [comment("late", { minute: 9 })],
      [comment("early", { minute: 1 })],
    );
    expect(merged.comments.map((c) => c.id)).toEqual(["early", "late"]);
  });

  it("キャラ情報が無い既存コメントを補完する（件数は増やさない）", () => {
    const bare = comment("k", {
      minute: 1,
      author_type: "character",
      author_user_id: null,
      author_character_id: "c1",
    });
    const merged = mergeComments([bare], [{ ...bare, character: MISAKI }]);
    expect(merged.added).toEqual([]);
    expect(merged.comments[0]?.character).toEqual(MISAKI);
  });
});

describe("removeComments", () => {
  it("指定したコメントと、その返信（子孫）をまとめて除く", () => {
    const list = [
      comment("a", { minute: 1 }),
      comment("a-r1", { minute: 2, parent_comment_id: "a" }),
      comment("a-r1-1", { minute: 3, parent_comment_id: "a-r1" }),
      comment("b", { minute: 4 }),
    ];
    const result = removeComments(list, ["a"]);
    expect(result.comments.map((c) => c.id)).toEqual(["b"]);
    expect(result.removedIds.sort()).toEqual(["a", "a-r1", "a-r1-1"]);
  });

  it("存在しない ID は何もしない", () => {
    const list = [comment("a", { minute: 1 })];
    const result = removeComments(list, ["zzz"]);
    expect(result.removedIds).toEqual([]);
    expect(result.comments).toHaveLength(1);
  });
});

describe("commentAuthorLabel", () => {
  const me = { userId: ME, displayName: "たろう", email: "taro@example.com" };

  it("キャラは handle", () => {
    const c = comment("k", {
      author_type: "character",
      author_user_id: null,
      author_character_id: "c1",
      character: MISAKI,
    });
    expect(commentAuthorLabel(c, me)).toBe("misaki_ol");
  });

  it("自分のコメントは自分の display_name", () => {
    expect(commentAuthorLabel(comment("m", { author_user_id: ME }), me)).toBe("たろう");
  });

  it("display_name が空ならメールのローカル部", () => {
    expect(
      commentAuthorLabel(comment("m", { author_user_id: ME }), { ...me, displayName: "  " }),
    ).toBe("taro");
  });

  it("他ユーザーは user_ + ID 先頭 6 桁（ハイフン除去）で匿名化", () => {
    expect(commentAuthorLabel(comment("o", { author_user_id: OTHER }), me)).toBe("user_ffeedd");
  });

  it("ログイン情報が未取得でも他人扱いで匿名化する", () => {
    expect(commentAuthorLabel(comment("m", { author_user_id: ME }), null)).toBe("user_a1b2c3");
  });
});

describe("isOwnComment / commentFromDto", () => {
  it("自分のユーザーコメントのみ true", () => {
    expect(isOwnComment(comment("m", { author_user_id: ME }), ME)).toBe(true);
    expect(isOwnComment(comment("o", { author_user_id: OTHER }), ME)).toBe(false);
    expect(isOwnComment(comment("m", { author_user_id: ME }), null)).toBe(false);
  });

  it("API のレスポンスを画面用の型に変換する", () => {
    const converted = commentFromDto({
      id: "n",
      post_id: POST_ID,
      parent_comment_id: null,
      author_type: "user",
      author_user_id: ME,
      author_character_id: null,
      body: "かわいい！",
      created_at: "2026-09-25T00:00:00Z",
    });
    expect(converted).toMatchObject({ id: "n", author_type: "user", character: null });
  });
});

describe("commentMentionLabel / replyMentionFor（返信の @メンション = 本文として全員に公開される）", () => {
  const me = { userId: ME, displayName: "山田たろう", email: "taro.yamada@example.com" };
  const mine = comment("m", { author_user_id: ME });
  const others = comment("o", { author_user_id: OTHER });
  const misaki = comment("k", {
    author_type: "character",
    author_user_id: null,
    author_character_id: "c1",
    character: MISAKI,
  });

  it("自分のコメントでも公開名（user_ + ID 先頭 6 桁）で、表示名やメールのローカル部を含まない", () => {
    const mention = commentMentionLabel(mine);
    expect(mention).toBe(anonymousUserName(ME));
    expect(mention).not.toContain("山田");
    expect(mention).not.toContain("taro");
    // 本人の画面の表示名（commentAuthorLabel）とは別物
    expect(commentAuthorLabel(mine, me)).toBe("山田たろう");
  });

  it("キャラは handle、他ユーザーは匿名名", () => {
    expect(commentMentionLabel(misaki)).toBe("misaki_ol");
    expect(commentMentionLabel(others)).toBe("user_ffeedd");
  });

  it("自分のコメントへの返信ではメンションを入れない", () => {
    expect(replyMentionFor(mine, ME)).toBeNull();
    expect(replyMentionFor(others, ME)).toBe("user_ffeedd");
    expect(replyMentionFor(misaki, ME)).toBe("misaki_ol");
  });

  it("ログイン情報が未取得でも、自分の名前は出さない", () => {
    expect(replyMentionFor(mine, null)).toBe(anonymousUserName(ME));
  });
});

/** supabase-js のクエリビルダーの最小の偽物（呼び出しを記録し、await で rows を返す） */
function fakeSupabase(rows: unknown[]) {
  const calls: [string, ...unknown[]][] = [];
  const builder: Record<string, unknown> = {};
  for (const method of ["select", "eq", "order", "limit", "abortSignal"]) {
    builder[method] = (...args: unknown[]) => {
      calls.push([method, ...args]);
      return builder;
    };
  }
  builder.then = (resolve: (value: unknown) => unknown, reject: (reason: unknown) => unknown) =>
    Promise.resolve({ data: rows, error: null }).then(resolve, reject);
  const client = {
    from: (table: string) => {
      calls.push(["from", table]);
      return builder;
    },
  } as unknown as TypedSupabaseClient;
  return { client, calls };
}

describe("fetchComments", () => {
  const row = (id: string, minute: number) => ({
    ...comment(id, { minute }),
    character: null,
  });

  it("上限を超える投稿でも最新の COMMENTS_LIMIT 件を取り（新しい順に取得）、時系列昇順で返す", async () => {
    // DB は新しい順に返す
    const { client, calls } = fakeSupabase([row("c", 3), row("b", 2), row("a", 1)]);
    const result = await fetchComments(client, POST_ID);
    expect(result.map((c) => c.id)).toEqual(["a", "b", "c"]);
    expect(calls).toContainEqual(["order", "created_at", { ascending: false }]);
    expect(calls).toContainEqual(["order", "id", { ascending: false }]);
    expect(calls).toContainEqual(["limit", COMMENTS_LIMIT]);
    expect(calls).toContainEqual(["eq", "post_id", POST_ID]);
  });

  it("不正な投稿 ID では問い合わせない", async () => {
    const { client, calls } = fakeSupabase([]);
    expect(await fetchComments(client, "not-a-uuid")).toEqual([]);
    expect(calls).toEqual([]);
  });
});
