import { useQueryClient, type InfiniteData, type QueryClient } from "@tanstack/react-query";
import { useCallback, useSyncExternalStore } from "react";
import { queryKeys } from "@/lib/queries/keys";

/**
 * ホームフィードの「引っ張って更新」相当の再読み込み（Instagram の Home タブ再タップ・プルリフレッシュ・
 * 長時間のバックグラウンドからの復帰）。
 *
 * - 対象は queryKeys.feed() 配下（フィード本体の無限クエリ + ストーリーズ行）。
 * - 無限クエリは 1 ページ目だけ残してから再取得する（読み込み済みの全ページを取り直さない。
 *   resetQueries と違ってスケルトンに戻さず、表示中の内容を残したまま差し替わる）。
 * - 同時に複数回呼ばれても 1 回にまとめる。進行中かどうかは useFeedRefreshing() で購読できる
 *   （MainShell のプルリフレッシュ表示が使う）。
 *
 * 全体の refetchOnWindowFocus は false のまま（何ページも読み込んだフィードをフォーカスのたびに取り直さない）。
 */

let inflight: Promise<void> | null = null;
const listeners = new Set<() => void>();

function emit(): void {
  for (const listener of listeners) listener();
}

function isInfiniteData(value: unknown): value is InfiniteData<unknown, unknown> {
  return (
    typeof value === "object" &&
    value !== null &&
    Array.isArray((value as { pages?: unknown }).pages) &&
    Array.isArray((value as { pageParams?: unknown }).pageParams)
  );
}

/** 無限クエリのデータを 1 ページ目だけに縮める（それ以外のデータはそのまま） */
export function keepFirstPage<T>(data: T): T {
  if (!isInfiniteData(data) || data.pages.length <= 1) return data;
  return { ...data, pages: data.pages.slice(0, 1), pageParams: data.pageParams.slice(0, 1) } as T;
}

/** ホームフィードを先頭から読み込み直す */
export function refreshFeed(queryClient: QueryClient): Promise<void> {
  if (inflight) return inflight;
  const feedKey = queryKeys.feed();
  // 取得中の次ページ読み込み等は中断してから縮める（古い応答で上書きされないように）
  inflight = queryClient
    .cancelQueries({ queryKey: feedKey })
    .then(() => {
      for (const query of queryClient.getQueryCache().findAll({ queryKey: feedKey })) {
        const data: unknown = query.state.data;
        if (isInfiniteData(data) && data.pages.length > 1) {
          queryClient.setQueryData(query.queryKey, keepFirstPage(data));
        }
      }
      return queryClient.refetchQueries({ queryKey: feedKey, type: "active" });
    })
    .catch((error: unknown) => {
      // 取得の失敗は各クエリのエラー表示（ErrorState）に任せる。ここでは記録だけ
      console.warn("[feed] refresh failed:", error);
    })
    .finally(() => {
      inflight = null;
      emit();
    });
  emit();
  return inflight;
}

function subscribe(listener: () => void): () => void {
  listeners.add(listener);
  return () => listeners.delete(listener);
}

/** refreshFeed() が進行中か */
export function useFeedRefreshing(): boolean {
  return useSyncExternalStore(
    subscribe,
    () => inflight !== null,
    () => false,
  );
}

/** 先頭付近とみなすスクロール量（px。端数のスクロールを先頭扱いにする） */
const TOP_TOLERANCE_PX = 2;

export function isScrolledToTop(): boolean {
  return window.scrollY <= TOP_TOLERANCE_PX;
}

/**
 * ホーム表示中に Home タブ / ロゴをタップしたときの動作（Instagram と同じ）:
 * スクロールしていれば先頭へ戻し、先頭にいればフィードを読み込み直す。
 */
export function useScrollTopOrRefreshFeed(): () => void {
  const queryClient = useQueryClient();
  return useCallback(() => {
    if (!isScrolledToTop()) {
      window.scrollTo({ top: 0, behavior: "smooth" });
      return;
    }
    void refreshFeed(queryClient);
  }, [queryClient]);
}
