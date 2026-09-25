import type { QueryClient } from "@tanstack/react-query";
import {
  characterPostCountQueryOptions,
  characterPostsQueryOptions,
  characterQueryOptions,
  normalizeHandle,
} from "./characters";
import { commentsQueryOptions } from "./comments";
import { isUuid, postQueryOptions } from "./posts";

/**
 * 遷移先の画面のデータを、リンクに触れた時点（pointerdown）で取りに行く。
 *
 * タップしてから遷移先の画面がマウントされてデータを取りに行くまでの時間（ルートの JS の読み込み・描画）を、
 * 通信と並行させて短くする。遷移先のフックと同じクエリ設定（xxxQueryOptions）を使うので、画面側の useQuery は
 * 進行中の取得・取得済みのキャッシュをそのまま使う。新しいキャッシュ（staleTime 以内）があれば何もしない。
 * 読み取り（Supabase の SELECT）だけで、副作用のある API は呼ばない。
 */

/** 投稿詳細（/posts/[postId]）: 投稿本体とコメント一覧 */
export function prefetchPostDetail(queryClient: QueryClient, postId: string): void {
  if (!isUuid(postId)) return;
  void queryClient.prefetchQuery(postQueryOptions(postId));
  void queryClient.prefetchQuery(commentsQueryOptions(postId));
}

/**
 * キャラクターのプロフィール（/c/[handle]）: キャラ本体・投稿数・最初のタブ（無料）のグリッド 1 ページ目。
 * 画面（CharacterProfileView）と同じく normalizeHandle した値をキーにする。
 */
export function prefetchCharacterProfile(queryClient: QueryClient, rawHandle: string): void {
  const handle = normalizeHandle(rawHandle);
  if (!handle) return;
  void queryClient.prefetchQuery(characterQueryOptions(handle));
  void queryClient.prefetchQuery(characterPostCountQueryOptions(handle));
  void queryClient.prefetchInfiniteQuery(characterPostsQueryOptions(handle, "free"));
}
