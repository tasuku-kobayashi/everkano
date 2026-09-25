/**
 * 文字列ユーティリティ（絵文字・サロゲートペアを壊さない）。
 *
 * `str.charAt(0)` / `str.length` は UTF-16 のコード単位で数えるため、「🌸さくら」の先頭は
 * サロゲートペアの片割れ（表示すると「�」）になり、長さも絵文字 1 つを 2 と数えてしまう。
 */

let segmenter: Intl.Segmenter | null | undefined;

function graphemeSegmenter(): Intl.Segmenter | null {
  if (segmenter === undefined) {
    segmenter =
      typeof Intl !== "undefined" && typeof Intl.Segmenter === "function"
        ? new Intl.Segmenter("ja", { granularity: "grapheme" })
        : null;
  }
  return segmenter;
}

/**
 * 先頭の 1 文字（書記素クラスタ。家族の絵文字 👨‍👩‍👧 や国旗なども 1 文字として扱う）。
 * 前後の空白は除く。空なら ""。アバターの頭文字に使う。
 */
export function firstGrapheme(value: string): string {
  const text = value.trim();
  if (!text) return "";
  const seg = graphemeSegmenter();
  if (seg) {
    const first = seg.segment(text)[Symbol.iterator]().next();
    if (!first.done) return first.value.segment;
  }
  // Intl.Segmenter の無い古いブラウザ: 少なくともサロゲートペアは分割しない
  return Array.from(text)[0] ?? "";
}

/**
 * コードポイント数（DB の char_length() と同じ数え方）。
 * 入力欄の文字数カウンター・上限チェックはサーバー / DB の制約と揃えるためこれを使う。
 */
export function codePointLength(value: string): number {
  let count = 0;
  for (const _ of value) count += 1;
  return count;
}
