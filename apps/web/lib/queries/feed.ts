import { useInfiniteQuery, useQuery } from "@tanstack/react-query";
import { getSupabaseBrowserClient } from "@/lib/supabase/client";
import type { TypedSupabaseClient } from "@/lib/supabase/types";
import { queryKeys } from "./keys";
import {
  POST_SELECT,
  toPosts,
  type PostAuthor,
  type PostCursor,
  type PostPage,
  type PostRowWithRelations,
} from "./posts";

/**
 * ホームフィード（全キャラの公開済み投稿を published_at DESC で無限スクロール）とストーリーズ行。
 *
 * ページングはオフセットではなくカーソル方式: 並び順は (published_at DESC, id DESC) で、
 * 次ページは「最後に表示した投稿より古いもの」を取る。予約投稿（未来の published_at）が
 * 公開されて先頭に増えても、ページ境界で重複・欠落が起きない。
 */

/** フィード 1 ページの件数 */
export const FEED_PAGE_SIZE = 10;

/** 24 時間以内に投稿したキャラはストーリーズ行でグラデーションのリングにする */
export const STORY_RECENT_MS = 24 * 60 * 60 * 1000;

/**
 * PostgREST のフィルター値をダブルクォートで囲む（`,` `.` `:` `(` `)` を含む値用）。
 * クォート内では `\` と `"` をバックスラッシュでエスケープする。
 */
export function quotePostgrestValue(value: string): string {
  return `"${value.replace(/\\/g, "\\\\").replace(/"/g, '\\"')}"`;
}

/** 取得した行と要求件数から次ページのカーソルを作る（要求件数に満たなければ最後のページ = null） */
export function buildNextCursor(
  rows: readonly { id: string; published_at: string }[],
  pageSize: number,
): PostCursor | null {
  if (rows.length < pageSize) return null;
  const last = rows[rows.length - 1];
  return last ? { publishedAt: last.published_at, id: last.id } : null;
}

/**
 * カーソルより「後ろ」（古い）行を取る `or` フィルター。
 *   published_at < cursor.publishedAt OR (published_at = cursor.publishedAt AND id < cursor.id)
 */
export function cursorOrFilter(cursor: PostCursor): string {
  const ts = quotePostgrestValue(cursor.publishedAt);
  const id = quotePostgrestValue(cursor.id);
  return `published_at.lt.${ts},and(published_at.eq.${ts},id.lt.${id})`;
}

export interface FetchPostPageOptions {
  cursor?: PostCursor | null;
  limit?: number;
  /** 特定キャラの投稿のみ（プロフィールのグリッド） */
  characterId?: string;
  /** true: 有料のみ / false: 無料のみ / 未指定: 両方 */
  isPaid?: boolean;
  signal?: AbortSignal;
}

/** 公開済み投稿を (published_at DESC, id DESC) で 1 ページ取得する（フィード・グリッド・発見タブ共通） */
export async function fetchPostPage(
  supabase: TypedSupabaseClient,
  { cursor, limit = FEED_PAGE_SIZE, characterId, isPaid, signal }: FetchPostPageOptions = {},
): Promise<PostPage> {
  let query = supabase.from("posts").select(POST_SELECT);
  if (characterId) query = query.eq("character_id", characterId);
  if (isPaid !== undefined) query = query.eq("is_paid", isPaid);
  if (cursor) query = query.or(cursorOrFilter(cursor));
  query = query
    .order("published_at", { ascending: false })
    .order("id", { ascending: false })
    .limit(limit);
  if (signal) query = query.abortSignal(signal);

  const { data, error } = await query;
  if (error) throw error;
  const rows: PostRowWithRelations[] = data ?? [];
  return { posts: toPosts(rows), nextCursor: buildNextCursor(rows, limit) };
}

/** ホームフィード（キー: queryKeys.feed()） */
export function useFeed() {
  return useInfiniteQuery({
    queryKey: queryKeys.feed(),
    queryFn: ({ pageParam, signal }) =>
      fetchPostPage(getSupabaseBrowserClient(), { cursor: pageParam, signal }),
    initialPageParam: null as PostCursor | null,
    getNextPageParam: (lastPage) => lastPage.nextCursor,
  });
}

// ---------------------------------------------------------------------------
// ストーリーズ行
// ---------------------------------------------------------------------------

export interface StoryItem {
  character: PostAuthor;
  /** そのキャラの最新の公開済み投稿（無ければ null → タップでプロフィールへ） */
  latestPost: { id: string; published_at: string } | null;
  /** 24 時間以内に投稿している */
  isRecent: boolean;
}

interface StoryRow extends PostAuthor {
  posts: { id: string; published_at: string }[] | null;
}

/** 24 時間以内に投稿したキャラを先頭に、最新投稿が新しい順（投稿の無いキャラは末尾）に並べる */
export function toStoryItems(rows: readonly StoryRow[], now: number = Date.now()): StoryItem[] {
  const items = rows.map<StoryItem>((row) => {
    const latest = row.posts?.[0] ?? null;
    const publishedMs = latest ? Date.parse(latest.published_at) : Number.NaN;
    return {
      character: { id: row.id, handle: row.handle, name: row.name, avatar_url: row.avatar_url },
      latestPost: latest ? { id: latest.id, published_at: latest.published_at } : null,
      isRecent: Number.isFinite(publishedMs) && now - publishedMs < STORY_RECENT_MS,
    };
  });
  const time = (item: StoryItem) =>
    item.latestPost ? Date.parse(item.latestPost.published_at) : Number.NEGATIVE_INFINITY;
  return items.sort((a, b) => {
    if (a.isRecent !== b.isRecent) return a.isRecent ? -1 : 1;
    const diff = time(b) - time(a);
    if (diff !== 0) return diff;
    return a.character.handle.localeCompare(b.character.handle);
  });
}

/** 有効キャラ一覧 + 各キャラの最新の公開済み投稿 1 件（posts の RLS で未来の予約投稿は含まれない） */
export async function fetchStories(
  supabase: TypedSupabaseClient,
  signal?: AbortSignal,
): Promise<StoryItem[]> {
  let query = supabase
    .from("characters")
    .select("id, handle, name, avatar_url, posts(id, published_at)")
    .order("published_at", { referencedTable: "posts", ascending: false })
    .limit(1, { referencedTable: "posts" })
    .order("created_at", { ascending: true })
    .limit(50);
  if (signal) query = query.abortSignal(signal);
  const { data, error } = await query;
  if (error) throw error;
  return toStoryItems(data ?? []);
}

/** ストーリーズ行（キー: queryKeys.stories()） */
export function useStories() {
  return useQuery({
    queryKey: queryKeys.stories(),
    queryFn: ({ signal }) => fetchStories(getSupabaseBrowserClient(), signal),
    staleTime: 60_000,
  });
}
