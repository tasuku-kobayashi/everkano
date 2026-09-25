import { PUBLIC_CHARACTER_COLUMNS, type PublicCharacter } from "@everkano/shared";
import { keepPreviousData, useInfiniteQuery, useQuery } from "@tanstack/react-query";
import { toAppError } from "@/lib/api/errors";
import { getSupabaseBrowserClient } from "@/lib/supabase/client";
import type { TypedSupabaseClient } from "@/lib/supabase/types";
import { fetchPostPage, quotePostgrestValue } from "./feed";
import { queryKeys } from "./keys";
import type { PostCursor } from "./posts";

/**
 * 検索（/search）。キャラクターを name / handle / bio の部分一致（ilike）で探す。
 * 検索語が空のときは「発見」グリッド（最近の公開済み投稿）を出す。
 */

/** 検索語の最大文字数（これを超える分は切り捨て） */
export const SEARCH_QUERY_MAX_LENGTH = 50;
/** 検索結果の最大件数 */
export const SEARCH_RESULT_LIMIT = 30;
/** 発見グリッド 1 ページの件数（3 列 × 10 行） */
export const EXPLORE_PAGE_SIZE = 30;

/**
 * 入力を検索語に正規化する（前後空白除去・連続空白の圧縮・先頭の @ 除去・長さ制限）。
 * `*` は PostgREST の like パターンでワイルドカード（% の別名）として解釈され、エスケープできないため除去する。
 */
export function normalizeSearchQuery(raw: string): string {
  return raw
    .replace(/\*/g, "")
    .replace(/\s+/g, " ")
    .trim()
    .replace(/^@+/, "")
    .slice(0, SEARCH_QUERY_MAX_LENGTH)
    .trim();
}

/** LIKE パターンの特殊文字（\ % _）をエスケープする（Postgres の既定のエスケープ文字は \） */
export function escapeLikePattern(value: string): string {
  return value.replace(/[\\%_]/g, (char) => `\\${char}`);
}

/** 部分一致（前後ワイルドカード）の ilike パターン。PostgREST のワイルドカード `*` を使う */
export function containsPattern(query: string): string {
  return `*${escapeLikePattern(query)}*`;
}

/** 検索対象の列 */
export const SEARCH_COLUMNS = ["name", "handle", "bio"] as const;

/**
 * name / handle / bio のいずれかに部分一致する `or` フィルター。
 * 値はダブルクォートで囲み、`,` `(` `)` 等を含む検索語でもフィルター構文が壊れないようにする。
 */
export function buildCharacterSearchFilter(query: string): string {
  const value = quotePostgrestValue(containsPattern(query));
  return SEARCH_COLUMNS.map((column) => `${column}.ilike.${value}`).join(",");
}

/**
 * 検索結果の並べ替え（関連度の高い順）:
 * handle 完全一致 → handle 前方一致 → name 前方一致 → handle / name 部分一致 → bio のみ一致。同順位はフォロワー数の多い順。
 */
export function rankSearchResults<
  T extends Pick<PublicCharacter, "handle" | "name" | "follower_count">,
>(characters: readonly T[], query: string): T[] {
  const q = query.toLowerCase();
  const score = (c: T): number => {
    const handle = c.handle.toLowerCase();
    const name = c.name.toLowerCase();
    if (handle === q || name === q) return 0;
    if (handle.startsWith(q)) return 1;
    if (name.startsWith(q)) return 2;
    if (handle.includes(q) || name.includes(q)) return 3;
    return 4;
  };
  return [...characters].sort((a, b) => score(a) - score(b) || b.follower_count - a.follower_count);
}

export async function searchCharacters(
  supabase: TypedSupabaseClient,
  rawQuery: string,
  signal?: AbortSignal,
): Promise<PublicCharacter[]> {
  const query = normalizeSearchQuery(rawQuery);
  if (!query) return [];
  let request = supabase
    .from("characters")
    .select(PUBLIC_CHARACTER_COLUMNS)
    .or(buildCharacterSearchFilter(query))
    .order("follower_count", { ascending: false })
    .limit(SEARCH_RESULT_LIMIT);
  if (signal) request = request.abortSignal(signal);
  const { data, error } = await request;
  if (error) throw toAppError(error);
  return rankSearchResults(data ?? [], query);
}

/** キャラ検索（キー: queryKeys.search(正規化済みの検索語)）。入力中は前回の結果を表示し続ける */
export function useCharacterSearch(rawQuery: string) {
  const query = normalizeSearchQuery(rawQuery);
  return useQuery({
    queryKey: queryKeys.search(query),
    queryFn: ({ signal }) => searchCharacters(getSupabaseBrowserClient(), query, signal),
    enabled: query.length > 0,
    placeholderData: keepPreviousData,
    staleTime: 60_000,
  });
}

/** 発見グリッド（検索語が空のとき。キー: queryKeys.search("")） */
export function useExplorePosts(enabled = true) {
  return useInfiniteQuery({
    queryKey: queryKeys.search(""),
    queryFn: ({ pageParam, signal }) =>
      fetchPostPage(getSupabaseBrowserClient(), {
        cursor: pageParam,
        limit: EXPLORE_PAGE_SIZE,
        signal,
      }),
    initialPageParam: null as PostCursor | null,
    getNextPageParam: (lastPage) => lastPage.nextCursor,
    enabled,
  });
}
