/**
 * キャプション・コメント本文のハッシュタグ（#虹）と @メンション（@misaki_ol）の切り出し。
 * Instagram と同じくリンク色（text-ig-link）で表示するために使う（リンクにはしない）。
 *
 * 判定は twitter-text に準拠した保守的なもの:
 * - ハッシュタグ: `#` / `＃` + 文字・数字・_ の並び（少なくとも 1 文字は文字を含む。「#1」は対象外）。
 *   直前が文字・数字・_・& のときは対象外（「C#」「今日の#」のような語中の # は色を付けない）
 * - メンション: `@` + 英数字・_・.（handle の形式。末尾の . は文の句点とみなして含めない）。
 *   直前が英数字・_・.・@ のときは対象外（メールアドレス「a@example.com」に色を付けない）
 *
 * 後読み（lookbehind）は iOS 16.3 以前の Safari で構文エラーになるため使わず、直前の文字を個別に調べる。
 */

export type RichTextTokenType = "text" | "hashtag" | "mention";

export interface RichTextToken {
  type: RichTextTokenType;
  value: string;
}

const TOKEN_RE = /[#＃][\p{L}\p{M}\p{N}_]+|@[A-Za-z0-9_](?:[A-Za-z0-9_.]{0,28}[A-Za-z0-9_])?/gu;
const HASHTAG_BLOCKED_BEFORE = /[&\p{L}\p{M}\p{N}_]/u;
const MENTION_BLOCKED_BEFORE = /[A-Za-z0-9_.@＠]/;
const HAS_LETTER = /[\p{L}\p{M}]/u;

/** 直前の 1 文字（サロゲートペアは 1 文字として扱う） */
function charBefore(text: string, index: number): string {
  if (index <= 0) return "";
  const low = text.charCodeAt(index - 1);
  if (index >= 2 && low >= 0xdc00 && low <= 0xdfff) {
    const high = text.charCodeAt(index - 2);
    if (high >= 0xd800 && high <= 0xdbff) return text.slice(index - 2, index);
  }
  return text.charAt(index - 1);
}

function isValidToken(text: string, index: number, value: string): RichTextTokenType | null {
  const before = charBefore(text, index);
  if (value.startsWith("@")) {
    return before && MENTION_BLOCKED_BEFORE.test(before) ? null : "mention";
  }
  if (before && HASHTAG_BLOCKED_BEFORE.test(before)) return null;
  return HAS_LETTER.test(value.slice(1)) ? "hashtag" : null;
}

/** 本文をテキスト / ハッシュタグ / メンションに分ける（連結すると元の文字列に戻る） */
export function tokenizeRichText(text: string): RichTextToken[] {
  const tokens: RichTextToken[] = [];
  let last = 0;
  for (const match of text.matchAll(TOKEN_RE)) {
    const index = match.index;
    const value = match[0];
    const type = isValidToken(text, index, value);
    if (!type) continue;
    if (index > last) tokens.push({ type: "text", value: text.slice(last, index) });
    tokens.push({ type, value });
    last = index + value.length;
  }
  if (last < text.length) tokens.push({ type: "text", value: text.slice(last) });
  return tokens;
}
