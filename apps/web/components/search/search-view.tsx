"use client";

import type { PublicCharacter } from "@everkano/shared";
import { useQueryClient } from "@tanstack/react-query";
import Link from "next/link";
import { useSearchParams } from "next/navigation";
import { useEffect, useMemo, useRef, useState } from "react";
import { PostGrid, PostGridSkeleton } from "@/components/post/post-grid";
import { AiBadge } from "@/components/ui/ai-badge";
import { Avatar } from "@/components/ui/avatar";
import { ErrorState } from "@/components/ui/error-state";
import { InfiniteScrollSentinel } from "@/components/ui/infinite-scroll-sentinel";
import { Skeleton } from "@/components/ui/skeleton";
import { formatCount } from "@/lib/format";
import { prefetchCharacterProfile } from "@/lib/queries/prefetch";
import { normalizeSearchQuery, useCharacterSearch, useExplorePosts } from "@/lib/queries/search";
import { SearchBar } from "./search-bar";
import { useDebouncedValue } from "./use-debounced-value";

/** 入力が止まってから検索するまでの時間（ms） */
export const SEARCH_DEBOUNCE_MS = 250;

/**
 * 検索（/search）。上部に検索バー、入力があればキャラの検索結果、空なら発見グリッド（最近の投稿）。
 * 検索語は URL の ?q= に反映する（プロフィールから戻っても結果が残る）。
 */
export function SearchView() {
  const searchParams = useSearchParams();
  const [input, setInput] = useState(() => searchParams.get("q") ?? "");
  const debounced = useDebouncedValue(input, SEARCH_DEBOUNCE_MS);
  const query = normalizeSearchQuery(debounced);
  const inputRef = useRef<HTMLInputElement>(null);

  // URL を置き換える（履歴は増やさない）
  useEffect(() => {
    const url = query ? `/search?q=${encodeURIComponent(query)}` : "/search";
    if (`${window.location.pathname}${window.location.search}` !== url) {
      window.history.replaceState(null, "", url);
    }
  }, [query]);

  return (
    <>
      <h1 className="sr-only">キャラクター検索</h1>
      <SearchBar
        ref={inputRef}
        value={input}
        onChange={setInput}
        onCancel={() => {
          setInput("");
          inputRef.current?.blur();
        }}
      />
      {query ? <SearchResults query={query} /> : <ExploreGrid />}
    </>
  );
}

function SearchResults({ query }: { query: string }) {
  const { data, isPending, isError, refetch, isRefetching, isPlaceholderData } =
    useCharacterSearch(query);

  if (isPending) return <SearchResultsSkeleton />;
  if (isError && !data) {
    return (
      <ErrorState
        compact
        message="検索できませんでした"
        onRetry={() => void refetch()}
        retrying={isRefetching}
      />
    );
  }
  if (data.length === 0 && !isPlaceholderData) {
    return (
      <p className="px-8 py-10 text-center text-[14px] text-ig-secondary" role="status">
        「{query}」の検索結果はありません
      </p>
    );
  }
  return (
    <ul
      aria-label="検索結果"
      aria-busy={isPlaceholderData || undefined}
      className={isPlaceholderData ? "opacity-60 transition-opacity" : "transition-opacity"}
      data-testid="search-results"
    >
      {data.map((character) => (
        <li key={character.id}>
          <SearchResultRow character={character} />
        </li>
      ))}
    </ul>
  );
}

function SearchResultRow({ character }: { character: PublicCharacter }) {
  const queryClient = useQueryClient();
  return (
    <Link
      href={`/c/${character.handle}`}
      onPointerDown={() => prefetchCharacterProfile(queryClient, character.handle)}
      className="flex items-center gap-3 px-4 py-2 active:bg-ig-elevated"
      data-testid="search-result"
    >
      <Avatar src={character.avatar_url} alt={character.name} size="md" />
      <div className="min-w-0 flex-1">
        <p className="flex min-w-0 items-center gap-1.5">
          <span className="min-w-0 truncate text-[14px] leading-[18px] font-semibold">
            {character.handle}
          </span>
          <AiBadge />
        </p>
        <p className="truncate text-[14px] leading-[18px] text-ig-secondary">
          {character.name} • フォロワー{formatCount(character.follower_count)}人
        </p>
      </div>
    </Link>
  );
}

function SearchResultsSkeleton() {
  return (
    <div aria-busy="true" aria-label="検索中">
      {[0, 1, 2, 3].map((i) => (
        <div key={i} className="flex items-center gap-3 px-4 py-2" aria-hidden="true">
          <Skeleton shape="circle" className="size-11 shrink-0" />
          <div className="flex-1 space-y-2">
            <Skeleton shape="text" className="w-28" />
            <Skeleton shape="text" className="w-40" />
          </div>
        </div>
      ))}
    </div>
  );
}

/** 発見グリッド（検索語が空のとき）: 最近の公開済み投稿を 3 列で。有料はぼかし + 鍵 → モーダル */
function ExploreGrid() {
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
  } = useExplorePosts();
  const posts = useMemo(() => data?.pages.flatMap((page) => page.posts) ?? [], [data]);

  if (isPending) return <PostGridSkeleton rows={5} />;
  if (isError && posts.length === 0) {
    return <ErrorState onRetry={() => void refetch()} retrying={isRefetching} />;
  }
  return (
    <section aria-label="発見" data-testid="explore-grid">
      <PostGrid posts={posts} />
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
    </section>
  );
}
