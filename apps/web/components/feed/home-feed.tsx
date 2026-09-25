"use client";

import { useMemo } from "react";
import { OPAQUE_HEADER } from "@/components/post/opaque-header";
import { PostCard } from "@/components/post/post-card";
import { AppHeader, HeaderIconButton } from "@/components/ui/app-header";
import { EmptyState } from "@/components/ui/empty-state";
import { ErrorState } from "@/components/ui/error-state";
import { CheckIcon, ImageIcon, PaperPlaneIcon } from "@/components/ui/icons";
import { InfiniteScrollSentinel } from "@/components/ui/infinite-scroll-sentinel";
import { PostCardSkeleton } from "@/components/ui/skeleton";
import { useFeed } from "@/lib/queries/feed";
import { StoriesRow } from "./stories-row";

/**
 * ホームフィード（/）: ロゴ + DM アイコンのヘッダー、ストーリーズ行、全キャラの投稿を published_at DESC で無限スクロール。
 */
export function HomeFeed() {
  const {
    data,
    isPending,
    isError,
    refetch,
    isRefetching,
    fetchNextPage,
    hasNextPage,
    isFetchingNextPage,
    isFetchNextPageError,
  } = useFeed();

  const posts = useMemo(() => data?.pages.flatMap((page) => page.posts) ?? [], [data]);

  return (
    <>
      <AppHeader
        variant="logo"
        className={OPAQUE_HEADER}
        right={
          <HeaderIconButton href="/dm" label="メッセージ">
            <PaperPlaneIcon size={25} />
          </HeaderIconButton>
        }
      />
      <StoriesRow />

      {isPending ? (
        <div aria-busy="true" aria-label="読み込み中" className="pt-1">
          <PostCardSkeleton />
          <PostCardSkeleton />
        </div>
      ) : isError && posts.length === 0 ? (
        <ErrorState onRetry={() => void refetch()} retrying={isRefetching} />
      ) : posts.length === 0 ? (
        <EmptyState
          icon={<ImageIcon size={30} strokeWidth={1.6} />}
          title="投稿はまだありません"
          description="キャラクターが投稿すると、ここに表示されます。"
        />
      ) : (
        <div className="pt-1" data-testid="feed">
          {posts.map((post, index) => (
            <PostCard key={post.id} post={post} priority={index === 0} />
          ))}
          <InfiniteScrollSentinel
            onLoadMore={() => void fetchNextPage()}
            hasMore={Boolean(hasNextPage)}
            loading={isFetchingNextPage}
            disabled={isFetchNextPageError}
          />
          {isFetchNextPageError ? (
            <ErrorState
              compact
              message="続きを読み込めませんでした"
              onRetry={() => void fetchNextPage()}
            />
          ) : null}
          {!hasNextPage ? <AllCaughtUp /> : null}
        </div>
      )}
    </>
  );
}

/** Instagram の「すべて確認済みです」 */
function AllCaughtUp() {
  return (
    <div className="flex flex-col items-center px-8 pt-6 pb-10 text-center" data-testid="feed-end">
      <span className="flex size-[58px] items-center justify-center rounded-full ig-story-ring p-[2px]">
        <span className="flex size-full items-center justify-center rounded-full bg-ig-bg">
          <CheckIcon size={28} strokeWidth={2.4} className="text-[#d62976]" />
        </span>
      </span>
      <p className="mt-3 text-[16px] leading-5 font-semibold">すべて確認済みです</p>
      <p className="mt-1 text-[14px] leading-[18px] text-ig-secondary">
        新しい投稿はまだありません。
      </p>
    </div>
  );
}
