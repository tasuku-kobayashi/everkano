import { describe, expect, it } from "vitest";
import type { TypedSupabaseClient } from "@/lib/supabase/types";
import {
  CHARACTER_GRID_PAGE_SIZE,
  characterPostCountKey,
  characterPostsKey,
  fetchCharacterPostCount,
  normalizeHandle,
} from "./characters";
import { fetchPostPage } from "./feed";
import { queryKeys } from "./keys";
import { POST_SELECT, POST_SELECT_CHARACTER_INNER } from "./posts";

/** supabase-js のクエリビルダーの最小の偽物（呼び出しを記録し、await で result を返す） */
function fakeSupabase(result: Record<string, unknown>) {
  const calls: [string, ...unknown[]][] = [];
  const builder: Record<string, unknown> = {};
  for (const method of ["select", "eq", "or", "order", "limit", "abortSignal"]) {
    builder[method] = (...args: unknown[]) => {
      calls.push([method, ...args]);
      return builder;
    };
  }
  builder.then = (resolve: (value: unknown) => unknown, reject: (reason: unknown) => unknown) =>
    Promise.resolve({ error: null, ...result }).then(resolve, reject);
  const client = {
    from: (table: string) => {
      calls.push(["from", table]);
      return builder;
    },
  } as unknown as TypedSupabaseClient;
  return { client, calls };
}

describe("normalizeHandle", () => {
  it("デコード・@ 除去・小文字化し、形式が不正なら null", () => {
    expect(normalizeHandle("%40Misaki_OL")).toBe("misaki_ol");
    expect(normalizeHandle("a")).toBeNull();
    expect(normalizeHandle("%E0%A4%A")).toBeNull();
  });
});

describe("プロフィールの投稿数・グリッド（キャラの ID を待たずに handle で取得する）", () => {
  it('キーはキャラ（handle）のサブキー（いいねの楽観的更新の対象 = "characters" 接頭辞）', () => {
    expect(characterPostCountKey("misaki_ol").slice(0, 3)).toEqual(
      queryKeys.character("misaki_ol"),
    );
    expect(characterPostsKey("misaki_ol", "paid")).toEqual([
      ...queryKeys.character("misaki_ol"),
      "posts",
      "paid",
    ]);
  });

  it("投稿数は handle でキャラを inner join して数える", async () => {
    const { client, calls } = fakeSupabase({ count: 7, data: null });
    expect(await fetchCharacterPostCount(client, "Misaki_OL")).toBe(7);
    expect(calls[0]).toEqual(["from", "posts"]);
    expect(String(calls[1]?.[1])).toContain("!inner(handle)");
    expect(calls[1]?.[2]).toEqual({ count: "exact", head: true });
    expect(calls).toContainEqual(["eq", "character.handle", "misaki_ol"]);
  });

  it("不正な handle では問い合わせない", async () => {
    const { client, calls } = fakeSupabase({ count: 3 });
    expect(await fetchCharacterPostCount(client, "!")).toBe(0);
    expect(calls).toEqual([]);
  });

  it("グリッドは handle で絞り込み（inner join）、タブで無料 / 有料を分ける", async () => {
    const { client, calls } = fakeSupabase({ data: [] });
    await fetchPostPage(client, {
      characterHandle: "misaki_ol",
      isPaid: true,
      limit: CHARACTER_GRID_PAGE_SIZE,
    });
    expect(calls[1]).toEqual(["select", POST_SELECT_CHARACTER_INNER]);
    expect(calls).toContainEqual(["eq", "character.handle", "misaki_ol"]);
    expect(calls).toContainEqual(["eq", "is_paid", true]);
    expect(calls).toContainEqual(["limit", CHARACTER_GRID_PAGE_SIZE]);
  });

  it("handle を指定しない一覧（フィード・発見タブ）は通常の埋め込み", async () => {
    const { client, calls } = fakeSupabase({ data: [] });
    await fetchPostPage(client, {});
    expect(calls[1]).toEqual(["select", POST_SELECT]);
    expect(calls.some(([method, column]) => method === "eq" && column === "character.handle")).toBe(
      false,
    );
  });
});
