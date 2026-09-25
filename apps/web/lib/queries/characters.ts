import { PUBLIC_CHARACTER_COLUMNS, type PublicCharacter } from "@everkano/shared";
import {
  infiniteQueryOptions,
  queryOptions,
  useInfiniteQuery,
  useQuery,
  useQueryClient,
} from "@tanstack/react-query";
import { useEffect } from "react";
import { toAppError } from "@/lib/api/errors";
import { getSupabaseBrowserClient } from "@/lib/supabase/client";
import type { TypedSupabaseClient } from "@/lib/supabase/types";
import { fetchPostPage, storiesQueryOptions } from "./feed";
import { queryKeys, type CharacterPostsTab } from "./keys";
import type { PostCursor } from "./posts";

/**
 * キャラクタープロフィール（/c/[handle]）。
 * characters は列権限で公開列しか読めないため、必ず PUBLIC_CHARACTER_COLUMNS を指定する。
 */

/** プロフィールのグリッド 1 ページの件数（3 列 × 8 行） */
export const CHARACTER_GRID_PAGE_SIZE = 24;

/** handle の形式（DB の check 制約と同じ） */
const HANDLE_RE = /^[a-z0-9_.]{2,30}$/;

/**
 * URL の handle を正規化する（デコード・前後空白除去・先頭の @ 除去・小文字化）。
 * 形式が不正なら null（DB へ問い合わせずに「ページが見つかりません」を出す）。
 */
export function normalizeHandle(raw: string): string | null {
  let value = raw;
  try {
    value = decodeURIComponent(raw);
  } catch {
    return null;
  }
  value = value.trim().replace(/^@/, "").toLowerCase();
  return HANDLE_RE.test(value) ? value : null;
}

/** handle でキャラクターを取得。存在しない / 非公開なら null */
export async function fetchCharacterByHandle(
  supabase: TypedSupabaseClient,
  handle: string,
  signal?: AbortSignal,
): Promise<PublicCharacter | null> {
  const normalized = normalizeHandle(handle);
  if (!normalized) return null;
  let query = supabase.from("characters").select(PUBLIC_CHARACTER_COLUMNS).eq("handle", normalized);
  if (signal) query = query.abortSignal(signal);
  const { data, error } = await query.maybeSingle();
  if (error) throw toAppError(error);
  return data;
}

/** キャラの公開済み投稿数（無料 + 有料。RLS で未公開の予約投稿は含まれない）。handle で数える */
export async function fetchCharacterPostCount(
  supabase: TypedSupabaseClient,
  handle: string,
  signal?: AbortSignal,
): Promise<number> {
  const normalized = normalizeHandle(handle);
  if (!normalized) return 0;
  let query = supabase
    .from("posts")
    .select("id, character:characters!posts_character_id_fkey!inner(handle)", {
      count: "exact",
      head: true,
    })
    .eq("character.handle", normalized);
  if (signal) query = query.abortSignal(signal);
  const { count, error } = await query;
  if (error) throw toAppError(error);
  return count ?? 0;
}

/** キャラクターのクエリ設定（useCharacter とプリフェッチ（lib/queries/prefetch.ts）で共通） */
export function characterQueryOptions(handle: string) {
  return queryOptions({
    queryKey: queryKeys.character(handle),
    queryFn: ({ signal }) => fetchCharacterByHandle(getSupabaseBrowserClient(), handle, signal),
    staleTime: 60_000,
  });
}

/** キャラクター（キー: queryKeys.character(handle)）。data が null なら存在しない */
export function useCharacter(handle: string) {
  return useQuery(characterQueryOptions(handle));
}

/*
 * 投稿数・グリッドは handle をキーにする（queryKeys.character(handle) のサブキー）。
 * キャラの ID が分かるのを待たずに、プロフィールを開いた時点でキャラ本体と並行して取得できる。
 * いいね等の楽観的更新（updatePostInCaches）は "characters" 接頭辞でグリッドのキャッシュも対象にする。
 */

/** 投稿数のキー */
export function characterPostCountKey(handle: string) {
  return [...queryKeys.character(handle), "post-count"] as const;
}

/** プロフィールのグリッド（無料 / 有料タブ）のキー */
export function characterPostsKey(handle: string, tab: CharacterPostsTab) {
  return [...queryKeys.character(handle), "posts", tab] as const;
}

export function characterPostCountQueryOptions(handle: string) {
  return queryOptions({
    queryKey: characterPostCountKey(handle),
    queryFn: ({ signal }) => fetchCharacterPostCount(getSupabaseBrowserClient(), handle, signal),
    enabled: Boolean(handle),
  });
}

export function characterPostsQueryOptions(handle: string, tab: CharacterPostsTab) {
  return infiniteQueryOptions({
    queryKey: characterPostsKey(handle, tab),
    queryFn: ({ pageParam, signal }) =>
      fetchPostPage(getSupabaseBrowserClient(), {
        characterHandle: handle,
        isPaid: tab === "paid",
        cursor: pageParam,
        limit: CHARACTER_GRID_PAGE_SIZE,
        signal,
      }),
    initialPageParam: null as PostCursor | null,
    getNextPageParam: (lastPage) => lastPage.nextCursor,
    enabled: Boolean(handle),
  });
}

export function useCharacterPostCount(handle: string) {
  return useQuery(characterPostCountQueryOptions(handle));
}

/** プロフィールのグリッド（無料 / 有料タブ） */
export function useCharacterPosts(handle: string, tab: CharacterPostsTab) {
  return useInfiniteQuery(characterPostsQueryOptions(handle, tab));
}

/**
 * プロフィールを開いた時点で、キャラ本体と並行して投稿数・最初のタブ（無料）のグリッド・
 * ストーリーズ（アバターのリング）の取得を始める。取得済み・取得中のものは何もしない。
 * handle が不正（null）なら何もしない。
 */
export function usePrefetchCharacterProfile(handle: string | null) {
  const queryClient = useQueryClient();
  useEffect(() => {
    if (!handle) return;
    void queryClient.prefetchQuery(characterPostCountQueryOptions(handle));
    void queryClient.prefetchInfiniteQuery(characterPostsQueryOptions(handle, "free"));
    void queryClient.prefetchQuery(storiesQueryOptions());
  }, [queryClient, handle]);
}
