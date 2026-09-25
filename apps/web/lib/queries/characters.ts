import { PUBLIC_CHARACTER_COLUMNS, type PublicCharacter } from "@everkano/shared";
import { useInfiniteQuery, useQuery } from "@tanstack/react-query";
import { getSupabaseBrowserClient } from "@/lib/supabase/client";
import type { TypedSupabaseClient } from "@/lib/supabase/types";
import { fetchPostPage } from "./feed";
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
  if (error) throw error;
  return data;
}

/** キャラの公開済み投稿数（無料 + 有料。RLS で未公開の予約投稿は含まれない） */
export async function fetchCharacterPostCount(
  supabase: TypedSupabaseClient,
  characterId: string,
  signal?: AbortSignal,
): Promise<number> {
  let query = supabase
    .from("posts")
    .select("id", { count: "exact", head: true })
    .eq("character_id", characterId);
  if (signal) query = query.abortSignal(signal);
  const { count, error } = await query;
  if (error) throw error;
  return count ?? 0;
}

/** キャラクター（キー: queryKeys.character(handle)）。data が null なら存在しない */
export function useCharacter(handle: string) {
  return useQuery({
    queryKey: queryKeys.character(handle),
    queryFn: ({ signal }) => fetchCharacterByHandle(getSupabaseBrowserClient(), handle, signal),
    staleTime: 60_000,
  });
}

/** 投稿数のキー（characterById のサブキー） */
export function characterPostCountKey(characterId: string) {
  return [...queryKeys.characterById(characterId), "post-count"] as const;
}

export function useCharacterPostCount(characterId: string | undefined) {
  return useQuery({
    queryKey: characterPostCountKey(characterId ?? ""),
    queryFn: ({ signal }) =>
      fetchCharacterPostCount(getSupabaseBrowserClient(), characterId ?? "", signal),
    enabled: Boolean(characterId),
  });
}

/** プロフィールのグリッド（無料 / 有料タブ。キー: queryKeys.characterPosts(id, tab)） */
export function useCharacterPosts(characterId: string | undefined, tab: CharacterPostsTab) {
  return useInfiniteQuery({
    queryKey: queryKeys.characterPosts(characterId ?? "", tab),
    queryFn: ({ pageParam, signal }) =>
      fetchPostPage(getSupabaseBrowserClient(), {
        characterId,
        isPaid: tab === "paid",
        cursor: pageParam,
        limit: CHARACTER_GRID_PAGE_SIZE,
        signal,
      }),
    initialPageParam: null as PostCursor | null,
    getNextPageParam: (lastPage) => lastPage.nextCursor,
    enabled: Boolean(characterId),
  });
}
