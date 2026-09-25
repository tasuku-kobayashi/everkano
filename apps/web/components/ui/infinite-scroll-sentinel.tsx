"use client";

import { useCallback, useEffect, useRef, useState } from "react";
import { cn } from "@/lib/cn";
import { Spinner } from "./spinner";

export interface UseInfiniteScrollOptions {
  /** 次ページを読み込む（React Query なら fetchNextPage） */
  onLoadMore: () => void;
  /** まだ続きがあるか（hasNextPage） */
  hasMore: boolean;
  /** 読み込み中か（isFetchingNextPage）。読み込み中は発火しない */
  loading: boolean;
  /** 画面端のどれだけ手前で読み込むか。既定 "800px 0px"（先読み） */
  rootMargin?: string;
  /** スクロールコンテナ（既定: ビューポート）。DM の過去ログなど独自スクロール領域で使う */
  root?: Element | null;
  /** true の間は監視しない（エラー表示中など） */
  disabled?: boolean;
}

/**
 * IntersectionObserver で番兵要素の可視化を監視し、onLoadMore を呼ぶ。
 * 戻り値の ref コールバックを番兵要素に付ける。
 *
 *   const sentinelRef = useInfiniteScroll({ onLoadMore: fetchNextPage, hasMore: !!hasNextPage, loading: isFetchingNextPage });
 *   <div ref={sentinelRef} />
 *
 * 読み込み完了後も番兵が見えたままなら（1 ページが短い場合）自動で続けて読み込む。
 */
export function useInfiniteScroll({
  onLoadMore,
  hasMore,
  loading,
  rootMargin = "800px 0px",
  root = null,
  disabled = false,
}: UseInfiniteScrollOptions): (node: Element | null) => void {
  const [node, setNode] = useState<Element | null>(null);
  const onLoadMoreRef = useRef(onLoadMore);
  useEffect(() => {
    onLoadMoreRef.current = onLoadMore;
  }, [onLoadMore]);

  useEffect(() => {
    if (!node || !hasMore || loading || disabled) return;
    if (typeof IntersectionObserver === "undefined") return;
    // loading が false に戻るたびに observer を作り直す = 番兵が見えていれば即座に再発火する
    const observer = new IntersectionObserver(
      (entries) => {
        if (entries.some((entry) => entry.isIntersecting)) {
          observer.disconnect();
          onLoadMoreRef.current();
        }
      },
      { root, rootMargin },
    );
    observer.observe(node);
    return () => observer.disconnect();
  }, [node, hasMore, loading, disabled, root, rootMargin]);

  return useCallback((element: Element | null) => setNode(element), []);
}

export interface InfiniteScrollSentinelProps extends UseInfiniteScrollOptions {
  className?: string;
  /** 読み込み中に表示するスピナー（既定 true） */
  showSpinner?: boolean;
}

/** 無限スクロールの番兵 + 読み込み中スピナー。リスト末尾（DM は先頭）に置く */
export function InfiniteScrollSentinel({
  className,
  showSpinner = true,
  ...options
}: InfiniteScrollSentinelProps) {
  const ref = useInfiniteScroll(options);
  return (
    <div className={cn("w-full", className)}>
      <div ref={ref} aria-hidden="true" className="h-px w-full" />
      {showSpinner && options.loading ? (
        <div className="flex justify-center py-4 text-ig-secondary">
          <Spinner size={24} />
        </div>
      ) : null}
    </div>
  );
}
