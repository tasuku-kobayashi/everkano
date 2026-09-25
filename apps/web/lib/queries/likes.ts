import { useMutation, useQueryClient, type QueryClient } from "@tanstack/react-query";
import { fetchMyAccount, type MyAccount } from "@/lib/auth/account";
import { getSupabaseBrowserClient } from "@/lib/supabase/client";
import type { TypedSupabaseClient } from "@/lib/supabase/types";
import { queryKeys } from "./keys";
import { updatePostInCaches, type Post } from "./posts";

/**
 * いいね（likes）。RLS により本人の行のみ作成・削除できるため、Supabase へ直接書き込む（Python API は経由しない）。
 * like_count は DB トリガー（likes_sync_post_like_count）が更新する。画面側は楽観的に ±1 する。
 */

/** Postgres の一意制約違反（既にいいね済み） */
const UNIQUE_VIOLATION = "23505";

/** いいね状態を反映した投稿を返す（状態が同じなら同じ参照 = 冪等。ロールバックにも使う） */
export function applyLike(post: Post, liked: boolean): Post {
  if (post.liked === liked) return post;
  return {
    ...post,
    liked,
    like_count: Math.max(0, post.like_count + (liked ? 1 : -1)),
  };
}

/** いいねする / 取り消す（既に同じ状態なら何もしない = 冪等） */
export async function setPostLiked(
  supabase: TypedSupabaseClient,
  { userId, postId, liked }: { userId: string; postId: string; liked: boolean },
): Promise<void> {
  if (liked) {
    const { error } = await supabase.from("likes").insert({ user_id: userId, post_id: postId });
    if (error && error.code !== UNIQUE_VIOLATION) throw error;
    return;
  }
  const { error } = await supabase
    .from("likes")
    .delete()
    .eq("user_id", userId)
    .eq("post_id", postId);
  if (error) throw error;
}

/** ログイン中ユーザーの ID（キャッシュ済みの自分のアカウント情報を優先） */
async function ensureMyUserId(queryClient: QueryClient): Promise<string> {
  const account = await queryClient.ensureQueryData<MyAccount | null>({
    queryKey: queryKeys.profile(),
    queryFn: fetchMyAccount,
  });
  if (!account) throw new Error("ログインが必要です");
  return account.userId;
}

export interface UsePostLikeOptions {
  /** 失敗時（ロールバック後）に呼ばれる。トースト表示など */
  onError?: (error: unknown) => void;
}

/**
 * 投稿のいいね切り替え（楽観的更新 + 失敗時ロールバック）。
 * 同じ投稿へのいいね操作は scope で直列化し、連打しても「押した順」にサーバーへ反映する。
 */
export function usePostLike(postId: string, { onError }: UsePostLikeOptions = {}) {
  const queryClient = useQueryClient();
  return useMutation({
    scope: { id: `like:${postId}` },
    mutationFn: async (liked: boolean) => {
      const userId = await ensureMyUserId(queryClient);
      await setPostLiked(getSupabaseBrowserClient(), { userId, postId, liked });
    },
    onMutate: (liked) => {
      updatePostInCaches(queryClient, postId, (post) => applyLike(post, liked));
    },
    onError: (error, liked) => {
      console.error("[likes] failed to update like:", error);
      updatePostInCaches(queryClient, postId, (post) => applyLike(post, !liked));
      onError?.(error);
    },
  });
}
