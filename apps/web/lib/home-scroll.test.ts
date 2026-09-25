import { QueryClient } from "@tanstack/react-query";
import { describe, expect, it } from "vitest";
import { hasCachedFeedPages } from "./home-scroll";
import { queryKeys } from "./queries/keys";

describe("hasCachedFeedPages（Home タブで戻ったときにスクロール位置を復元してよいか）", () => {
  it("フィードの読み込み済みページがキャッシュにあるときだけ true", () => {
    const client = new QueryClient();
    expect(hasCachedFeedPages(client)).toBe(false);

    // ストーリーズ行（同じ feed 接頭辞）だけでは復元しない
    client.setQueryData(queryKeys.stories(), [{ id: "c1" }]);
    expect(hasCachedFeedPages(client)).toBe(false);

    client.setQueryData(queryKeys.feed(), { pages: [], pageParams: [] });
    expect(hasCachedFeedPages(client)).toBe(false);

    client.setQueryData(queryKeys.feed(), { pages: [{ posts: [] }], pageParams: [null] });
    expect(hasCachedFeedPages(client)).toBe(true);

    // gcTime 経過などでキャッシュが消えたら復元しない
    client.removeQueries({ queryKey: queryKeys.feed(), exact: true });
    expect(hasCachedFeedPages(client)).toBe(false);
  });
});
