import type { AuthorType, CommentDTO, Database } from "@everkano/shared";
import {
  queryOptions,
  useMutation,
  useQuery,
  useQueryClient,
  type QueryClient,
} from "@tanstack/react-query";
import { useEffect, useRef } from "react";
import { toAppError } from "@/lib/api/errors";
import { api } from "@/lib/api";
import type { MyAccount } from "@/lib/auth/account";
import { anonymousUserName } from "@/lib/format";
import { getSupabaseBrowserClient } from "@/lib/supabase/client";
import type { TypedSupabaseClient } from "@/lib/supabase/types";
import { queryKeys } from "./keys";
import { uniqueSuffix } from "./messages";
import { isUuid, updatePostInCaches, type Post, type PostAuthor } from "./posts";

/**
 * 投稿のコメント。
 *
 * - 読み取り: Supabase 直結（RLS: 公開投稿のコメントのみ）。最新 COMMENTS_LIMIT 件を時系列昇順で表示する。
 * - 作成: Python API `POST /comments`（Gate #1 モデレーション + 監査ログ）。クライアントに insert 権限は無い。
 * - 削除: 本人のコメントのみ Supabase へ直接 delete（RLS で許可。監査ログは DB トリガーが記録）。
 * - 同期: Supabase Realtime（postgres_changes）で INSERT / DELETE を受け取り、キャッシュへ重複なくマージする。
 *   キャラクターの自動返信（API のバックグラウンド処理）もこの経路で届く。
 */

export type CommentRow = Database["public"]["Tables"]["comments"]["Row"];

export const COMMENT_SELECT =
  "id, post_id, parent_comment_id, author_type, author_user_id, author_character_id, body, created_at, character:characters!comments_author_character_id_fkey(id, handle, name, avatar_url)" as const;

/**
 * 1 投稿あたりに読み込むコメントの上限（MVP ではページングしない）。
 * 上限を超える投稿では「最新の」COMMENTS_LIMIT 件を表示する（古い方を残すと、自分が投稿したばかりの
 * コメントやキャラの返信が再読み込みで消えてしまうため）。
 */
export const COMMENTS_LIMIT = 500;

/** コメントの作成者の種別（API の AuthorType と同じ） */
export type CommentAuthorType = AuthorType;

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

  const threads = new Map<string, CommentThread>();
  for (const comment of sorted) {
    const rootId = threadRootId(byId, comment);
    if (rootId === comment.id) {
      threads.set(comment.id, { root: comment, replies: [] });
    }
  }
  for (const comment of sorted) {
    const rootId = threadRootId(byId, comment);
    if (rootId !== comment.id) threads.get(rootId)?.replies.push(comment);
  }
  return [...threads.values()];
}

/**
 * コメントが属するトップレベルのコメント ID（祖先をたどる。buildCommentThreads と同じ規則）。
 * 親が見えない返信はそれ自身がトップレベル。byId は id → コメントの Map か、コメントの配列。
 */
export function threadRootId(
  comments: ReadonlyMap<string, PostComment> | readonly PostComment[],
  comment: PostComment,
): string {
  const byId =
    comments instanceof Map
      ? (comments as ReadonlyMap<string, PostComment>)
      : new Map((comments as readonly PostComment[]).map((c) => [c.id, c]));
  let current = comment;
  const seen = new Set<string>([current.id]);
  while (current.parent_comment_id) {
    const parent = byId.get(current.parent_comment_id);
    if (!parent || seen.has(parent.id)) break; // 親が見えない / 循環（あり得ないが念のため）
    seen.add(parent.id);
    current = parent;
  }
  return current.id;
}

/**
 * コメント作成者の表示名。
 * - キャラ: handle
 * - 自分: 自分の display_name（未設定ならメールのローカル部）
 * - 他ユーザー: profiles は本人しか読めないため `user_` + ID 先頭 6 桁で匿名表示
 *
 * 自分の名前（display_name / メールのローカル部）を含み得るので、本人の画面の表示にだけ使うこと。
 * コメント本文（返信の @メンション）など他ユーザーに見える所には commentMentionLabel を使う。
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

/**
 * 返信の @メンションに使う公開用の名前（コメント本文として保存され、全ユーザーに表示される）。
 * - キャラ: handle
 * - ユーザー: 自分のコメントでも常に `user_` + ID 先頭 6 桁（他ユーザーからの見え方と同じ）
 *
 * commentAuthorLabel と違い、自分の display_name やメールアドレスは絶対に使わない
 * （display_name の既定値はメールの @ より前なので、本文に入ると他ユーザーへ漏れる）。
 */
export function commentMentionLabel(
  comment: Pick<PostComment, "author_type" | "author_user_id" | "character">,
): string {
  if (comment.author_type === "character") return comment.character?.handle ?? "unknown";
  if (!comment.author_user_id) return "user";
  return anonymousUserName(comment.author_user_id);
}

/**
 * 「返信する」で入力欄の先頭に入れるメンション（例: `misaki_ol`）。
 * 自分のコメントへの返信ではメンションを入れない（自分宛ての匿名名は本人には意味が無い）ため null。
 */
export function replyMentionFor(
  comment: Pick<PostComment, "author_type" | "author_user_id" | "character">,
  myUserId: string | null | undefined,
): string | null {
  if (isOwnComment(comment, myUserId)) return null;
  return commentMentionLabel(comment);
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

/**
 * 投稿のコメント（最新 COMMENTS_LIMIT 件）を時系列昇順で返す。
 * 新しい順に上限まで取り、画面の並び（昇順）に並べ直す。
 */
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
    .order("created_at", { ascending: false })
    .order("id", { ascending: false })
    .limit(COMMENTS_LIMIT);
  if (signal) query = query.abortSignal(signal);
  const { data, error } = await query;
  if (error) throw toAppError(error);
  return (data ?? []).map((row) => commentFromRow(row, row.character ?? null)).reverse();
}

/** 投稿のコメント一覧のクエリ設定（useComments とプリフェッチで共通） */
export function commentsQueryOptions(postId: string) {
  return queryOptions({
    queryKey: queryKeys.comments(postId),
    queryFn: ({ signal }) => fetchComments(getSupabaseBrowserClient(), postId, signal),
  });
}

/** 投稿のコメント一覧（キー: queryKeys.comments(postId)） */
export function useComments(postId: string, enabled = true) {
  return useQuery({ ...commentsQueryOptions(postId), enabled });
}

/**
 * コメント一覧の取得を投稿本体の取得と並行して始める（投稿詳細を直接開いたとき、
 * 投稿の取得を待ってからコメントを取りに行く直列の待ちをなくす）。取得済み・取得中なら何もしない。
 */
export function usePrefetchComments(postId: string) {
  const queryClient = useQueryClient();
  useEffect(() => {
    if (!isUuid(postId)) return;
    void queryClient.prefetchQuery(commentsQueryOptions(postId));
  }, [queryClient, postId]);
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
    const topic = `post-comments:${postId}:${uniqueSuffix()}`;
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
      if (error) throw toAppError(error);
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
