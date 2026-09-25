import { InfiniteQueryObserver, QueryClient, QueryObserver } from "@tanstack/react-query";
import { describe, expect, it } from "vitest";
import { queryKeys } from "@/lib/queries/keys";
import { keepFirstPage, refreshFeed } from "./feed-refresh";

describe("keepFirstPage", () => {
  it("無限クエリのデータを 1 ページ目だけにする", () => {
    const data = { pages: [["a"], ["b"], ["c"]], pageParams: [null, "b", "c"] };
    expect(keepFirstPage(data)).toEqual({ pages: [["a"]], pageParams: [null] });
    expect(data.pages).toHaveLength(3); // 元のデータは変更しない
  });

  it("無限クエリ以外・1 ページだけのデータはそのまま返す", () => {
    const single = { pages: [["a"]], pageParams: [null] };
    expect(keepFirstPage(single)).toBe(single);
    const list = [1, 2, 3];
    expect(keepFirstPage(list)).toBe(list);
    expect(keepFirstPage(undefined)).toBeUndefined();
  });
});

describe("refreshFeed", () => {
  it("読み込み済みのページを捨てて 1 ページ目だけを取り直し、ストーリーズ行も更新する", async () => {
    const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
    let version = 0;
    const fetchedPages: unknown[] = [];

    const feed = new InfiniteQueryObserver(client, {
      queryKey: queryKeys.feed(),
      initialPageParam: 0,
      queryFn: ({ pageParam }) => {
        fetchedPages.push(pageParam);
        return { page: pageParam, version };
      },
      getNextPageParam: (last: { page: number }) => (last.page < 5 ? last.page + 1 : undefined),
    });
    const stories = new QueryObserver(client, {
      queryKey: queryKeys.stories(),
      queryFn: () => ({ stories: version }),
    });
    const unsubscribeFeed = feed.subscribe(() => undefined);
    const unsubscribeStories = stories.subscribe(() => undefined);

    await feed.refetch();
    await feed.fetchNextPage();
    await feed.fetchNextPage();
    expect(feed.getCurrentResult().data?.pages).toHaveLength(3);

    version = 1;
    fetchedPages.length = 0;
    // 同時に呼ばれても 1 回にまとめる
    await Promise.all([refreshFeed(client), refreshFeed(client)]);

    const result = feed.getCurrentResult().data;
    expect(result?.pages).toEqual([{ page: 0, version: 1 }]);
    expect(fetchedPages).toEqual([0]);
    expect(client.getQueryData(queryKeys.stories())).toEqual({ stories: 1 });

    unsubscribeFeed();
    unsubscribeStories();
    client.clear();
  });
});
