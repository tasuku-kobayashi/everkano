import { describe, expect, it } from "vitest";
import {
  buildCharacterSearchFilter,
  containsPattern,
  escapeLikePattern,
  normalizeSearchQuery,
  rankSearchResults,
  SEARCH_QUERY_MAX_LENGTH,
} from "./search";

describe("escapeLikePattern", () => {
  it("% と _ をエスケープする", () => {
    expect(escapeLikePattern("100%_off")).toBe("100\\%\\_off");
  });

  it("バックスラッシュ自体もエスケープする", () => {
    expect(escapeLikePattern("a\\b")).toBe("a\\\\b");
  });

  it("通常の文字はそのまま", () => {
    expect(escapeLikePattern("美咲 misaki.ol")).toBe("美咲 misaki.ol");
  });
});

describe("normalizeSearchQuery", () => {
  it("前後の空白を除き、連続する空白を 1 つにする", () => {
    expect(normalizeSearchQuery("  美咲 \t  OL  ")).toBe("美咲 OL");
  });

  it("先頭の @ を除く（@handle で検索できるように）", () => {
    expect(normalizeSearchQuery("@misaki_ol")).toBe("misaki_ol");
  });

  it("PostgREST のワイルドカード * を除く", () => {
    expect(normalizeSearchQuery("*")).toBe("");
    expect(normalizeSearchQuery("mi*saki")).toBe("misaki");
  });

  it("長すぎる入力は切り詰める", () => {
    expect(normalizeSearchQuery("あ".repeat(80))).toHaveLength(SEARCH_QUERY_MAX_LENGTH);
  });
});

describe("buildCharacterSearchFilter", () => {
  it("name / handle / bio の部分一致を or で結ぶ", () => {
    expect(buildCharacterSearchFilter("美咲")).toBe(
      'name.ilike."*美咲*",handle.ilike."*美咲*",bio.ilike."*美咲*"',
    );
  });

  it("% と _ はワイルドカードにならないようにエスケープされ、クォート用に \\ が二重化される", () => {
    // LIKE エスケープ: \_ → PostgREST のクォート内エスケープ: \\_
    expect(containsPattern("a_b")).toBe("*a\\_b*");
    expect(buildCharacterSearchFilter("a_b")).toContain('handle.ilike."*a\\\\_b*"');
    expect(buildCharacterSearchFilter("50%")).toContain('name.ilike."*50\\\\%*"');
  });

  it("カンマ・括弧・ダブルクォートを含んでもフィルター構文が壊れない", () => {
    const filter = buildCharacterSearchFilter('a,b)"c');
    expect(filter).toBe('name.ilike."*a,b)\\"c*",handle.ilike."*a,b)\\"c*",bio.ilike."*a,b)\\"c*"');
  });
});

describe("rankSearchResults", () => {
  const c = (handle: string, name: string, follower_count = 0) => ({
    handle,
    name,
    follower_count,
  });

  it("handle 完全一致 → handle 前方一致 → name 前方一致 → 部分一致 → bio のみ", () => {
    const ranked = rankSearchResults(
      [
        c("bio_only", "ほげ", 999),
        c("xx_mi", "テスト"),
        c("mi", "ミ"),
        c("abc", "miko"),
        c("misaki_ol", "美咲"),
      ],
      "mi",
    );
    expect(ranked.map((x) => x.handle)).toEqual(["mi", "misaki_ol", "abc", "xx_mi", "bio_only"]);
  });

  it("同順位はフォロワー数の多い順", () => {
    const ranked = rankSearchResults([c("ma_a", "A", 10), c("ma_b", "B", 500)], "ma");
    expect(ranked.map((x) => x.handle)).toEqual(["ma_b", "ma_a"]);
  });
});
