import type { Post } from "@/lib/queries/posts";

/**
 * 投稿のスクリーンリーダー向けの名前。
 * グリッドのタイルやフィードの画像ボタンがすべて同じ名前（「美咲の投稿を開く」×N）にならないよう、
 * キャラ名に加えてキャプションの冒頭を含める。
 */

type LabelPost = Pick<Post, "caption" | "is_paid" | "price_tokens" | "character">;

/** キャプションの冒頭（改行・連続する空白は 1 つの空白にまとめ、絵文字を途中で切らない） */
export function captionExcerpt(caption: string | null | undefined, maxChars = 30): string {
  const text = (caption ?? "").replace(/\s+/g, " ").trim();
  if (!text) return "";
  const chars = Array.from(text);
  return chars.length > maxChars ? `${chars.slice(0, maxChars).join("").trimEnd()}…` : text;
}

function withExcerpt(label: string, caption: string | null | undefined): string {
  const excerpt = captionExcerpt(caption);
  return excerpt ? `${label}: ${excerpt}` : label;
}

/**
 * 投稿を開く操作の名前（フィードの画像・グリッドの無料タイル）。
 * 例: 「美咲の投稿を開く: 今日もおつかれさま。…」/ 有料「美咲の有料コンテンツの詳細を見る: …」
 */
export function postOpenLabel(post: LabelPost): string {
  const name = post.character.name;
  return withExcerpt(
    post.is_paid ? `${name}の有料コンテンツの詳細を見る` : `${name}の投稿を開く`,
    post.caption,
  );
}

/** グリッドの有料タイルの名前。例: 「美咲の有料コンテンツ（300 tokens）: 年に2回のひとり旅。…」 */
export function paidTileLabel(post: LabelPost): string {
  return withExcerpt(
    `${post.character.name}の有料コンテンツ（${post.price_tokens} tokens）`,
    post.caption,
  );
}
