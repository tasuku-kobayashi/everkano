import {
  useQuery,
  useQueryClient,
  type InfiniteData,
  type QueryClient,
} from "@tanstack/react-query";
import { getSupabaseBrowserClient } from "@/lib/supabase/client";
import type { TypedSupabaseClient } from "@/lib/supabase/types";
import { queryKeys } from "./keys";

/**
 * 投稿（posts）の取得と React Query キャッシュ操作。
 *
 * - 読み取りはブラウザから Supabase へ直接（RLS: 公開済み かつ 有効キャラの投稿のみ見える）。
 * - `my_likes:likes(user_id)` は likes の RLS（本人の行のみ参照可）により「自分のいいね」だけが返るため、
 *   1 回のクエリで「いいね済みか」まで分かる。
 * - キャラクターは公開列のみ埋め込む（characters の select('*') は列権限エラー）。
 */

/** 投稿に埋め込むキャラクター（公開列のうち表示に必要なもの） */
export const POST_AUTHOR_COLUMNS = "id, handle, name, avatar_url" as const;

/** 投稿の select 文字列（フィード・投稿詳細・グリッドで共通） */
export const POST_SELECT =
  "id, character_id, image_url, caption, is_paid, price_tokens, like_count, comment_count, published_at, character:characters!posts_character_id_fkey(id, handle, name, avatar_url), my_likes:likes(user_id)" as const;

export interface PostAuthor {
  id: string;
  handle: string;
  name: string;
  avatar_url: string;
}

/** 画面で扱う投稿（キャラ情報といいね状態を含む） */
export interface Post {
  id: string;
  character_id: string;
  /** 有料投稿の場合はプレビュー画像（本体は post_private_assets でクライアントから到達不能） */
  image_url: string;
  caption: string | null;
  is_paid: boolean;
  price_tokens: number;
  like_count: number;
  comment_count: number;
  published_at: string;
  character: PostAuthor;
  /** 自分がいいね済みか */
  liked: boolean;
}

/** カーソルページング（published_at DESC, id DESC）の 1 ページ */
export interface PostPage {
  posts: Post[];
  /** 次ページのカーソル。null なら最後のページ */
  nextCursor: PostCursor | null;
}

/** 最後に表示した投稿の (published_at, id)。published_at は DB が返した文字列をそのまま使う（マイクロ秒を保持） */
export interface PostCursor {
  publishedAt: string;
  id: string;
}

/** POST_SELECT で返る 1 行の形（supabase-js の型推論と同じ形を明示しておく） */
export interface PostRowWithRelations {
  id: string;
  character_id: string;
  image_url: string;
  caption: string | null;
  is_paid: boolean;
  price_tokens: number;
  like_count: number;
  comment_count: number;
  published_at: string;
  character: PostAuthor | null;
  my_likes: { user_id: string }[] | null;
}

const UUID_RE = /^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/i;

/** UUID 形式か（不正な値で問い合わせると 22P02 エラーになるため事前に弾く） */
export function isUuid(value: string): boolean {
  return UUID_RE.test(value);
}

/** DB の行 → 画面用の Post。キャラが見えない（非公開化された等）行は null */
export function toPost(row: PostRowWithRelations): Post | null {
  if (!row.character) return null;
  return {
    id: row.id,
    character_id: row.character_id,
    image_url: row.image_url,
    caption: row.caption,
    is_paid: row.is_paid,
    price_tokens: row.price_tokens,
    like_count: row.like_count,
    comment_count: row.comment_count,
    published_at: row.published_at,
    character: row.character,
    liked: (row.my_likes?.length ?? 0) > 0,
  };
}

export function toPosts(rows: readonly PostRowWithRelations[]): Post[] {
  const posts: Post[] = [];
  for (const row of rows) {
    const post = toPost(row);
    if (post) posts.push(post);
  }
  return posts;
}

/** 投稿 1 件を取得。存在しない / 見えない（未公開・非公開キャラ）/ 不正な ID は null */
export async function fetchPost(
  supabase: TypedSupabaseClient,
  postId: string,
  signal?: AbortSignal,
): Promise<Post | null> {
  if (!isUuid(postId)) return null;
  let query = supabase.from("posts").select(POST_SELECT).eq("id", postId);
  if (signal) query = query.abortSignal(signal);
  const { data, error } = await query.maybeSingle();
  if (error) throw error;
  return data ? toPost(data) : null;
}

// ---------------------------------------------------------------------------
// キャッシュ操作（いいね・コメント数の楽観的更新など）
// ---------------------------------------------------------------------------

/** 投稿の一覧（ページング）を保持しているキャッシュのキー接頭辞 */
function postListKeyPrefixes() {
  return [queryKeys.feed(), ["characters"] as const, queryKeys.search("")];
}

function isPostPageData(value: unknown): value is InfiniteData<PostPage> {
  if (!value || typeof value !== "object" || !("pages" in value)) return false;
  const pages = (value as { pages: unknown }).pages;
  return (
    Array.isArray(pages) &&
    pages.every(
      (page) =>
        page !== null &&
        typeof page === "object" &&
        Array.isArray((page as { posts?: unknown }).posts),
    )
  );
}

/** InfiniteData<PostPage> 内の該当投稿を置き換える（変更が無ければ同じ参照を返す） */
export function mapPostInPages(
  data: InfiniteData<PostPage>,
  postId: string,
  updater: (post: Post) => Post,
): InfiniteData<PostPage> {
  let changed = false;
  const pages = data.pages.map((page) => {
    let pageChanged = false;
    const posts = page.posts.map((post) => {
      if (post.id !== postId) return post;
      const next = updater(post);
      if (next !== post) pageChanged = true;
      return next;
    });
    if (!pageChanged) return page;
    changed = true;
    return { ...page, posts };
  });
  return changed ? { ...data, pages } : data;
}

/**
 * キャッシュ中のすべての該当投稿（投稿詳細・フィード・プロフィールのグリッド・検索の発見タブ）を更新する。
 * いいね・コメント数の楽観的更新に使う。
 */
export function updatePostInCaches(
  queryClient: QueryClient,
  postId: string,
  updater: (post: Post) => Post,
): void {
  queryClient.setQueryData<Post | null>(queryKeys.post(postId), (old) =>
    old ? updater(old) : old,
  );
  for (const prefix of postListKeyPrefixes()) {
    for (const [key, data] of queryClient.getQueriesData({ queryKey: prefix })) {
      if (!isPostPageData(data)) continue;
      const next = mapPostInPages(data, postId, updater);
      if (next !== data) queryClient.setQueryData(key, next);
    }
  }
}

/** キャッシュ済みの一覧から投稿を探す（投稿詳細を開いた瞬間の placeholder 用） */
export function findCachedPost(queryClient: QueryClient, postId: string): Post | undefined {
  for (const prefix of postListKeyPrefixes()) {
    for (const [, data] of queryClient.getQueriesData({ queryKey: prefix })) {
      if (!isPostPageData(data)) continue;
      for (const page of data.pages) {
        const found = page.posts.find((post) => post.id === postId);
        if (found) return found;
      }
    }
  }
  return undefined;
}

/** 投稿詳細（キー: queryKeys.post(postId)）。data が null なら「存在しない / 見られない」 */
export function usePost(postId: string) {
  const queryClient = useQueryClient();
  return useQuery<Post | null, Error, Post | null, ReturnType<typeof queryKeys.post>>({
    queryKey: queryKeys.post(postId),
    queryFn: ({ signal }) => fetchPost(getSupabaseBrowserClient(), postId, signal),
    // フィード等から遷移した場合は、取得完了までキャッシュ済みの投稿を先に表示する
    placeholderData: () => findCachedPost(queryClient, postId),
  });
}
