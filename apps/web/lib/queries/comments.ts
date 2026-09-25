import type { CommentDTO, Database } from "@everkano/shared";
import { useMutation, useQuery, useQueryClient, type QueryClient } from "@tanstack/react-query";
import { useEffect, useRef } from "react";
import { api } from "@/lib/api";
import type { MyAccount } from "@/lib/auth/account";
import { anonymousUserName } from "@/lib/format";
import { getSupabaseBrowserClient } from "@/lib/supabase/client";
import type { TypedSupabaseClient } from "@/lib/supabase/types";
import { queryKeys } from "./keys";
import { isUuid, updatePostInCaches, type Post, type PostAuthor } from "./posts";

/**
 * 投稿のコメント。
 *
 * - 読み取り: Supabase 直結（RLS: 公開投稿のコメントのみ）。時系列昇順。
 * - 作成: Python API `POST /comments`（Gate #1 モデレーション + 監査ログ）。クライアントに insert 権限は無い。
 * - 削除: 本人のコメントのみ Supabase へ直接 delete（RLS で許可。監査ログは DB トリガーが記録）。
 * - 同期: Supabase Realtime（postgres_changes）で INSERT / DELETE を受け取り、キャッシュへ重複なくマージする。
 *   キャラクターの自動返信（API のバックグラウンド処理）もこの経路で届く。
 */

export type CommentRow = Database["public"]["Tables"]["comments"]["Row"];

export const COMMENT_SELECT =
  "id, post_id, parent_comment_id, author_type, author_user_id, author_character_id, body, created_at, character:characters!comments_author_character_id_fkey(id, handle, name, avatar_url)" as const;

/** 1 投稿あたりに読み込むコメントの上限（MVP ではページングしない） */
export const COMMENTS_LIMIT = 500;

export type CommentAuthorType = "user" | "character";

export interface PostComment {
  id: string;
  post_id: string;
  parent_comment_id: string | null;
  author_type: CommentAuthorType;
  author_user_id: string | null;
  author_character_id: string | null;
  body: string;
  created_at: string;
  /** キャラクターのコメントの場合の作成者（公開列のみ）。ユーザーのコメントは null */
  character: PostAuthor | null;
}

export interface CommentThread {
  /** トップレベルのコメント */
  root: PostComment;
  /** 返信（返信への返信も含め、同じトップレベルの下に時系列昇順でまとめる = Instagram 方式） */
  replies: PostComment[];
}

// ---------------------------------------------------------------------------
// 純粋関数（ユニットテスト対象）
// ---------------------------------------------------------------------------

function toAuthorType(value: string): CommentAuthorType {
  return value === "character" ? "character" : "user";
}

/** created_at 昇順（同時刻は id 順）で比較 */
export function compareComments(a: PostComment, b: PostComment): number {
  const diff = Date.parse(a.created_at) - Date.parse(b.created_at);
  if (diff !== 0 && Number.isFinite(diff)) return diff;
  return a.id < b.id ? -1 : a.id > b.id ? 1 : 0;
}

/** DB 行（Realtime の payload 等）→ PostComment */
export function commentFromRow(
  row: Pick<
    CommentRow,
    | "id"
    | "post_id"
    | "parent_comment_id"
    | "author_type"
    | "author_user_id"
    | "author_character_id"
    | "body"
    | "created_at"
  >,
  character: PostAuthor | null = null,
): PostComment {
  return {
    id: row.id,
    post_id: row.post_id,
    parent_comment_id: row.parent_comment_id,
    author_type: toAuthorType(row.author_type),
    author_user_id: row.author_user_id,
    author_character_id: row.author_character_id,
    body: row.body,
    created_at: row.created_at,
    character: toAuthorType(row.author_type) === "character" ? character : null,
  };
}

/** API のレスポンス（CommentDTO）→ PostComment */
export function commentFromDto(dto: CommentDTO, character: PostAuthor | null = null): PostComment {
  return commentFromRow(dto, character);
}

/**
 * コメントをキャッシュへマージする（id で重複排除し、時系列昇順に並べ直す）。
 * API のレスポンスと Realtime の INSERT のどちらが先に届いても 1 件だけになる。
 * 既存のコメントにキャラ情報が無く、新しい方にある場合は補完する。
 */
export function mergeComments(
  current: readonly PostComment[],
  incoming: readonly PostComment[],
): { comments: PostComment[]; added: PostComment[] } {
  const byId = new Map(current.map((comment) => [comment.id, comment]));
  const added: PostComment[] = [];
  let changed = false;
  for (const comment of incoming) {
    const existing = byId.get(comment.id);
    if (!existing) {
      byId.set(comment.id, comment);
      added.push(comment);
      changed = true;
    } else if (!existing.character && comment.character) {
      byId.set(comment.id, { ...existing, character: comment.character });
      changed = true;
    }
  }
  if (!changed) return { comments: [...current], added };
  return { comments: [...byId.values()].sort(compareComments), added };
}

/**
 * コメントを削除する。DB は parent_comment_id の on delete cascade で返信も消えるため、子孫もまとめて除く。
 * removedIds は実際にキャッシュから除いた ID。
 */
export function removeComments(
  current: readonly PostComment[],
  ids: readonly string[],
): { comments: PostComment[]; removedIds: string[] } {
  const removed = new Set<string>();
  for (const id of ids) {
    if (current.some((comment) => comment.id === id)) removed.add(id);
  }
  if (removed.size === 0) return { comments: [...current], removedIds: [] };
  // 子孫を幅優先でたどる
  let grew = true;
  while (grew) {
    grew = false;
    for (const comment of current) {
      if (
        !removed.has(comment.id) &&
        comment.parent_comment_id &&
        removed.has(comment.parent_comment_id)
      ) {
        removed.add(comment.id);
        grew = true;
      }
    }
  }
  return {
    comments: current.filter((comment) => !removed.has(comment.id)),
    removedIds: [...removed],
  };
}

/**
 * コメントをスレッドにまとめる（Instagram 方式: 返信は 1 段だけインデントし、トップレベルの下に並べる）。
 * 返信への返信は、祖先をたどったトップレベルのスレッドに入れる。親が見つからない返信はトップレベル扱い。
 */
export function buildCommentThreads(comments: readonly PostComment[]): CommentThread[] {
  const sorted = [...comments].sort(compareComments);
  const byId = new Map(sorted.map((comment) => [comment.id, comment]));

  const rootIdOf = (comment: PostComment): string => {
    let current = comment;
    const seen = new Set<string>([current.id]);
    while (current.parent_comment_id) {
      const parent = byId.get(current.parent_comment_id);
      if (!parent || seen.has(parent.id)) break; // 親が見えない / 循環（あり得ないが念のため）
      seen.add(parent.id);
      current = parent;
    }
    return current.id;
  };

  const threads = new Map<string, CommentThread>();
  for (const comment of sorted) {
    const rootId = rootIdOf(comment);
    if (rootId === comment.id) {
      threads.set(comment.id, { root: comment, replies: [] });
    }
  }
  for (const comment of sorted) {
    const rootId = rootIdOf(comment);
    if (rootId !== comment.id) threads.get(rootId)?.replies.push(comment);
  }
  return [...threads.values()];
}

/**
 * コメント作成者の表示名。
 * - キャラ: handle
 * - 自分: 自分の display_name（未設定ならメールのローカル部）
 * - 他ユーザー: profiles は本人しか読めないため `user_` + ID 先頭 6 桁で匿名表示
 */
export function commentAuthorLabel(
  comment: Pick<PostComment, "author_type" | "author_user_id" | "character">,
  me: Pick<MyAccount, "userId" | "displayName" | "email"> | null | undefined,
): string {
  if (comment.author_type === "character") return comment.character?.handle ?? "unknown";
  if (!comment.author_user_id) return "user";
  if (me && comment.author_user_id === me.userId) {
    return me.displayName?.trim() || me.email?.split("@")[0] || anonymousUserName(me.userId);
  }
  return anonymousUserName(comment.author_user_id);
}

/** 自分のコメントか */
export function isOwnComment(
  comment: Pick<PostComment, "author_type" | "author_user_id">,
  userId: string | null | undefined,
): boolean {
  return Boolean(userId && comment.author_type === "user" && comment.author_user_id === userId);
}

// ---------------------------------------------------------------------------
// 取得・キャッシュ
// ---------------------------------------------------------------------------

export async function fetchComments(
  supabase: TypedSupabaseClient,
  postId: string,
  signal?: AbortSignal,
): Promise<PostComment[]> {
  if (!isUuid(postId)) return [];
  let query = supabase
    .from("comments")
    .select(COMMENT_SELECT)
    .eq("post_id", postId)
    .order("created_at", { ascending: true })
    .order("id", { ascending: true })
    .limit(COMMENTS_LIMIT);
  if (signal) query = query.abortSignal(signal);
  const { data, error } = await query;
  if (error) throw error;
  return (data ?? []).map((row) => commentFromRow(row, row.character ?? null));
}

/** 投稿のコメント一覧（キー: queryKeys.comments(postId)） */
export function useComments(postId: string, enabled = true) {
  return useQuery({
    queryKey: queryKeys.comments(postId),
    queryFn: ({ signal }) => fetchComments(getSupabaseBrowserClient(), postId, signal),
    enabled,
  });
}

/** コメントをキャッシュへ追加し、実際に増えた件数だけ投稿の comment_count を増やす */
export function addCommentsToCache(
  queryClient: QueryClient,
  postId: string,
  incoming: readonly PostComment[],
): PostComment[] {
  let added: PostComment[] = [];
  queryClient.setQueryData<PostComment[]>(queryKeys.comments(postId), (old) => {
    const result = mergeComments(old ?? [], incoming);
    added = result.added;
    return result.comments;
  });
  if (added.length > 0) {
    const count = added.length;
    updatePostInCaches(queryClient, postId, (post) => ({
      ...post,
      comment_count: post.comment_count + count,
    }));
  }
  return added;
}

/** コメントをキャッシュから除き（返信も連鎖）、除いた件数だけ comment_count を減らす */
export function removeCommentsFromCache(
  queryClient: QueryClient,
  postId: string,
  ids: readonly string[],
): string[] {
  let removedIds: string[] = [];
  queryClient.setQueryData<PostComment[]>(queryKeys.comments(postId), (old) => {
    if (!old) return old;
    const result = removeComments(old, ids);
    removedIds = result.removedIds;
    return result.comments;
  });
  if (removedIds.length > 0) {
    const count = removedIds.length;
    updatePostInCaches(queryClient, postId, (post) => ({
      ...post,
      comment_count: Math.max(0, post.comment_count - count),
    }));
  }
  return removedIds;
}

/** Realtime で届いたキャラコメントの作成者情報を解決する（キャッシュ → 投稿者 → DB の順） */
async function resolveCommentCharacter(
  queryClient: QueryClient,
  postId: string,
  characterId: string,
): Promise<PostAuthor | null> {
  const cached = queryClient
    .getQueryData<PostComment[]>(queryKeys.comments(postId))
    ?.find((comment) => comment.character?.id === characterId)?.character;
  if (cached) return cached;
  const post = queryClient.getQueryData<Post | null>(queryKeys.post(postId));
  if (post?.character.id === characterId) return post.character;
  const { data, error } = await getSupabaseBrowserClient()
    .from("characters")
    .select("id, handle, name, avatar_url")
    .eq("id", characterId)
    .maybeSingle();
  if (error) {
    console.error("[comments] failed to resolve character:", error);
    return null;
  }
  return data;
}

export interface CommentsRealtimeOptions {
  /** 新しいコメントがキャッシュに追加されたとき（重複は除く） */
  onInsert?: (comment: PostComment) => void;
}

/**
 * 投稿のコメントを Realtime で購読する（INSERT: post_id で絞り込み / DELETE: 主キーのみ届くため全件購読し ID で照合）。
 * 再接続時は取りこぼし防止のため一覧を再取得する。
 */
export function useCommentsRealtime(
  postId: string,
  { onInsert }: CommentsRealtimeOptions = {},
  enabled = true,
) {
  const queryClient = useQueryClient();
  const onInsertRef = useRef(onInsert);
  useEffect(() => {
    onInsertRef.current = onInsert;
  }, [onInsert]);

  useEffect(() => {
    if (!enabled || !isUuid(postId)) return;
    const supabase = getSupabaseBrowserClient();
    // 同名トピックは既存チャンネルが再利用されるため、購読ごとに一意な名前にする（StrictMode の二重実行対策）
    const topic = `post-comments:${postId}:${Math.random().toString(36).slice(2, 10)}`;
    let subscribedOnce = false;
    let disposed = false;

    const channel = supabase
      .channel(topic)
      .on(
        "postgres_changes",
        { event: "INSERT", schema: "public", table: "comments", filter: `post_id=eq.${postId}` },
        (payload) => {
          const row = payload.new as CommentRow;
          void (async () => {
            const character =
              row.author_type === "character" && row.author_character_id
                ? await resolveCommentCharacter(queryClient, postId, row.author_character_id)
                : null;
            if (disposed) return;
            const [added] = addCommentsToCache(queryClient, postId, [
              commentFromRow(row, character),
            ]);
            if (added) onInsertRef.current?.(added);
          })();
        },
      )
      .on(
        "postgres_changes",
        { event: "DELETE", schema: "public", table: "comments" },
        (payload) => {
          const id = (payload.old as Partial<CommentRow>).id;
          if (id) removeCommentsFromCache(queryClient, postId, [id]);
        },
      )
      .subscribe((status, error) => {
        if (status === "SUBSCRIBED") {
          if (subscribedOnce) {
            void queryClient.invalidateQueries({ queryKey: queryKeys.comments(postId) });
          }
          subscribedOnce = true;
        } else if (status === "CHANNEL_ERROR" || status === "TIMED_OUT") {
          console.error(`[comments] realtime ${status}:`, error?.message ?? "");
        }
      });

    return () => {
      disposed = true;
      void supabase.removeChannel(channel);
    };
  }, [postId, enabled, queryClient]);
}

/** コメント投稿（Python API 経由）。成功したらキャッシュへ追加する */
export function useCreateComment(postId: string) {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: (input: { body: string; parentCommentId?: string | null }) =>
      api.createComment({
        post_id: postId,
        body: input.body,
        parent_comment_id: input.parentCommentId ?? null,
      }),
    onSuccess: (response) => {
      addCommentsToCache(queryClient, postId, [commentFromDto(response.comment)]);
    },
  });
}

/** 自分のコメントの削除（Supabase 直結。RLS で本人のみ許可） */
export function useDeleteComment(postId: string) {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: async (commentId: string) => {
      const { data, error } = await getSupabaseBrowserClient()
        .from("comments")
        .delete()
        .eq("id", commentId)
        .select("id");
      if (error) throw error;
      // RLS で対象外（他人のコメント・既に削除済み）の場合はエラーにならず 0 件になる
      if (!data || data.length === 0) {
        throw new Error("コメントを削除できませんでした");
      }
      return commentId;
    },
    onSuccess: (commentId) => {
      removeCommentsFromCache(queryClient, postId, [commentId]);
    },
  });
}
