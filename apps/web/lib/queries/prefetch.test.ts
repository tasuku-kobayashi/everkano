import type { DmThread } from "@everkano/shared";
import type { QueryClient } from "@tanstack/react-query";
import { beforeEach, describe, expect, it, vi } from "vitest";
import type * as ApiClientModule from "@/lib/api/client";
import { createQueryClient } from "@/lib/query-client";
import {
  characterPostCountKey,
  characterPostCountQueryOptions,
  characterPostsKey,
  characterPostsQueryOptions,
  characterQueryOptions,
} from "./characters";
import { characterStateQueryOptions } from "./character-state";
import { commentsQueryOptions } from "./comments";
import { dmCharacterKey, dmCharacterQueryOptions, prefetchDmConversation } from "./dm";
import { queryKeys } from "./keys";
import { messagesQueryOptions } from "./messages";
import { postQueryOptions } from "./posts";
import { prefetchCharacterProfile, prefetchPostDetail } from "./prefetch";

/**
 * 遷移先のデータの先読み（lib/queries/prefetch.ts・prefetchDmConversation）。
 * - 遷移先の画面のフック（usePost / useComments / useCharacter / useMessages 等）と同じキーでキャッシュに入る
 *   （画面側は進行中の取得・取得済みのデータをそのまま使い、同じデータを 2 回取りに行かない）
 * - 不正な ID・handle では何もしない / 新しいキャッシュがあれば通信しない
 * - 読み取り（Supabase の SELECT）だけで、副作用のある API（会話の作成など）は呼ばない
 */

interface RecordedQuery {
  table: string;
  ops: [string, ...unknown[]][];
}

const state = vi.hoisted(() => ({
  queries: [] as { table: string; ops: [string, ...unknown[]][] }[],
  apiCalls: [] as string[],
}));

/** supabase-js のクエリビルダーの偽物（どのメソッドも自分を返し、await すると空の結果） */
vi.mock("@/lib/supabase/client", () => {
  const from = (table: string) => {
    const entry = { table, ops: [] as [string, ...unknown[]][] };
    state.queries.push(entry);
    const builder: object = new Proxy(
      {},
      {
        get(_target, prop) {
          if (prop === "then") {
            return (resolve: (value: unknown) => unknown, reject: (reason: unknown) => unknown) =>
              Promise.resolve({ data: [], error: null, count: 0 }).then(resolve, reject);
          }
          if (prop === "maybeSingle" || prop === "single") {
            return () => Promise.resolve({ data: null, error: null });
          }
          return (...args: unknown[]) => {
            entry.ops.push([String(prop), ...args]);
            return builder;
          };
        },
      },
    );
    return builder;
  };
  return { getSupabaseBrowserClient: () => ({ from }) };
});

/** Python API は先読みから呼ばれてはいけない（呼ばれたら記録して失敗させる） */
vi.mock("@/lib/api/client", async (importOriginal) => {
  const original = await importOriginal<typeof ApiClientModule>();
  const api = new Proxy(
    {},
    {
      get(_target, prop) {
        return () => {
          state.apiCalls.push(String(prop));
          return Promise.reject(new Error(`unexpected API call: ${String(prop)}`));
        };
      },
    },
  );
  return { ...original, api };
});

const POST_ID = "00000000-0000-4000-8001-000000000805";
const CHARACTER_ID = "00000000-0000-4000-8000-0000000000c1";
const CONVERSATION_ID = "00000000-0000-4000-9000-000000000001";

function cachedKeys(queryClient: QueryClient): string[] {
  return queryClient
    .getQueryCache()
    .getAll()
    .map((query) => JSON.stringify(query.queryKey))
    .sort();
}

function key(queryKey: readonly unknown[]): string {
  return JSON.stringify(queryKey);
}

async function settle(queryClient: QueryClient): Promise<void> {
  await vi.waitFor(() => expect(queryClient.isFetching()).toBe(0));
}

function tables(): string[] {
  return state.queries.map((query) => query.table);
}

function eqFilters(query: RecordedQuery | undefined): unknown[][] {
  return (query?.ops ?? []).filter(([op]) => op === "eq").map(([, ...args]) => args);
}

let queryClient: QueryClient;

beforeEach(() => {
  state.queries.length = 0;
  state.apiCalls.length = 0;
  // アプリと同じ既定設定（staleTime 30 秒など）
  queryClient = createQueryClient();
});

describe("prefetchPostDetail（フィード・グリッド・検索 → 投稿詳細）", () => {
  it("投稿本体とコメント一覧を、投稿詳細の画面と同じキーで取りに行く", async () => {
    prefetchPostDetail(queryClient, POST_ID);
    await settle(queryClient);

    expect(cachedKeys(queryClient)).toEqual(
      [key(postQueryOptions(POST_ID).queryKey), key(commentsQueryOptions(POST_ID).queryKey)].sort(),
    );
    expect(tables().sort()).toEqual(["comments", "posts"]);
    const posts = state.queries.find((query) => query.table === "posts");
    const comments = state.queries.find((query) => query.table === "comments");
    expect(eqFilters(posts)).toEqual([["id", POST_ID]]);
    expect(eqFilters(comments)).toEqual([["post_id", POST_ID]]);
  });

  it("取得済み（新しい）なら通信しない・連続で触れても 1 回だけ", async () => {
    prefetchPostDetail(queryClient, POST_ID);
    prefetchPostDetail(queryClient, POST_ID);
    await settle(queryClient);
    prefetchPostDetail(queryClient, POST_ID);
    await settle(queryClient);
    expect(tables().sort()).toEqual(["comments", "posts"]);
  });

  it("投稿 ID が UUID でなければ何もしない", async () => {
    prefetchPostDetail(queryClient, "not-a-post");
    await settle(queryClient);
    expect(cachedKeys(queryClient)).toEqual([]);
    expect(state.queries).toEqual([]);
  });
});

describe("prefetchCharacterProfile（投稿・コメント・検索・DM → キャラのプロフィール）", () => {
  it("キャラ本体・投稿数・「無料」タブのグリッドを、プロフィール画面と同じキー（正規化した handle）で取りに行く", async () => {
    prefetchCharacterProfile(queryClient, "%40Misaki_OL");
    await settle(queryClient);

    expect(cachedKeys(queryClient)).toEqual(
      [
        key(characterQueryOptions("misaki_ol").queryKey),
        key(characterPostCountQueryOptions("misaki_ol").queryKey),
        key(characterPostsQueryOptions("misaki_ol", "free").queryKey),
      ].sort(),
    );
    expect(characterPostCountKey("misaki_ol")).toEqual(
      characterPostCountQueryOptions("misaki_ol").queryKey,
    );
    expect(characterPostsKey("misaki_ol", "free")).toEqual(
      characterPostsQueryOptions("misaki_ol", "free").queryKey,
    );
    const character = state.queries.find((query) => query.table === "characters");
    expect(eqFilters(character)).toEqual([["handle", "misaki_ol"]]);
    // 投稿数・グリッドは posts を handle で絞る（キャラの ID を待たない）
    expect(tables().filter((table) => table === "posts")).toHaveLength(2);
  });

  it("キャラ本体のキャッシュが新しければ、キャラ本体は取り直さない", async () => {
    queryClient.setQueryData(characterQueryOptions("misaki_ol").queryKey, null);
    prefetchCharacterProfile(queryClient, "misaki_ol");
    await settle(queryClient);
    expect(tables()).not.toContain("characters");
  });

  it("handle の形式が不正なら何もしない", async () => {
    prefetchCharacterProfile(queryClient, "a");
    prefetchCharacterProfile(queryClient, "%E0%A4%A");
    await settle(queryClient);
    expect(cachedKeys(queryClient)).toEqual([]);
    expect(state.queries).toEqual([]);
  });
});

describe("prefetchDmConversation（DM 一覧・プロフィールの「DMする」→ DM 会話）", () => {
  function thread(overrides: Partial<DmThread> = {}): DmThread {
    return {
      conversation_id: CONVERSATION_ID,
      character_id: CHARACTER_ID,
      character_handle: "misaki_ol",
      character_name: "美咲",
      character_avatar_url: "https://example.com/a.png",
      last_message_body: "おつかれさま",
      last_message_sender_type: "character",
      last_message_at: "2026-09-25T03:00:00Z",
      unread_count: 0,
      ...overrides,
    };
  }

  it("会話のまだ無いキャラ（おすすめ）: ヘッダーのキャラ情報と今の状況だけを取りに行き、会話は作らない", async () => {
    prefetchDmConversation(queryClient, CHARACTER_ID);
    await settle(queryClient);

    expect(cachedKeys(queryClient)).toEqual(
      [
        key(dmCharacterQueryOptions(CHARACTER_ID).queryKey),
        key(characterStateQueryOptions(CHARACTER_ID).queryKey),
      ].sort(),
    );
    expect(dmCharacterKey(CHARACTER_ID)).toEqual(dmCharacterQueryOptions(CHARACTER_ID).queryKey);
    expect(tables()).toEqual(["characters", "character_states"]);
    expect(eqFilters(state.queries[0])).toEqual([["id", CHARACTER_ID]]);
    expect(eqFilters(state.queries[1])).toEqual([["character_id", CHARACTER_ID]]);
    expect(state.apiCalls, "POST /conversations（副作用）は先読みしない").toEqual([]);
  });

  it("DM 一覧にある会話: キャラ情報に加えて、会話画面と同じキーでメッセージの 1 ページ目も取りに行く", async () => {
    queryClient.setQueryData(queryKeys.dmThreads(), [
      thread({ conversation_id: "00000000-0000-4000-9000-000000000002", character_id: "other" }),
      thread(),
    ]);
    prefetchDmConversation(queryClient, CHARACTER_ID);
    await settle(queryClient);

    expect(cachedKeys(queryClient)).toEqual(
      [
        key(queryKeys.dmThreads()),
        key(dmCharacterQueryOptions(CHARACTER_ID).queryKey),
        key(characterStateQueryOptions(CHARACTER_ID).queryKey),
        key(messagesQueryOptions(CONVERSATION_ID).queryKey),
      ].sort(),
    );
    expect(tables().sort()).toEqual(["character_states", "characters", "messages"]);
    expect(state.apiCalls).toEqual([]);
  });
});
