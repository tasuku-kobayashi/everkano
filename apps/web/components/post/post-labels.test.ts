import { describe, expect, it } from "vitest";
import { captionExcerpt, paidTileLabel, postOpenLabel } from "./post-labels";

const character = { id: "c1", handle: "misaki_ol", name: "美咲", avatar_url: "https://x/a.png" };
const post = (caption: string | null, is_paid = false) => ({
  caption,
  is_paid,
  price_tokens: is_paid ? 300 : 0,
  character,
});

describe("captionExcerpt", () => {
  it("改行・連続する空白をまとめ、長ければ省略する", () => {
    expect(captionExcerpt("今日もおつかれさま。\nお風呂あがりに", 30)).toBe(
      "今日もおつかれさま。 お風呂あがりに",
    );
    expect(captionExcerpt("あいうえおかきくけこ", 5)).toBe("あいうえお…");
    expect(captionExcerpt(null)).toBe("");
    expect(captionExcerpt("  \n ")).toBe("");
  });

  it("絵文字（サロゲートペア）を途中で切らない", () => {
    expect(captionExcerpt("🍋🍋🍋", 2)).toBe("🍋🍋…");
  });
});

describe("postOpenLabel / paidTileLabel（スクリーンリーダーで投稿を区別できる名前）", () => {
  it("同じキャラの投稿でもキャプションで区別できる", () => {
    const a = postOpenLabel(post("出社前の、いつものカフェ。"));
    const b = postOpenLabel(post("提案、通りました〜！"));
    expect(a).toBe("美咲の投稿を開く: 出社前の、いつものカフェ。");
    expect(b).not.toBe(a);
  });

  it("キャプションが無ければキャラ名のみ", () => {
    expect(postOpenLabel(post(null))).toBe("美咲の投稿を開く");
  });

  it("有料投稿", () => {
    expect(postOpenLabel(post("ひとり旅", true))).toBe(
      "美咲の有料コンテンツの詳細を見る: ひとり旅",
    );
    expect(paidTileLabel(post("ひとり旅", true))).toBe(
      "美咲の有料コンテンツ（300 tokens）: ひとり旅",
    );
  });
});
