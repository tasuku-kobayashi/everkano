/**
 * キャプションの「… 続きを読む」の省略位置の計算（PostCaption から使う）。
 *
 * 計測は同期レイアウト（getBoundingClientRect）を伴うため、回数を減らす:
 * 1. 全文を 1 回レイアウトし、Range で各文字の行を読み取って省略位置を見積もる（読むだけなので追加のレイアウトなし）
 * 2. 見積もり位置が収まり、1 つ先が収まらないことを確かめる（通常はこの 2 回で確定）
 * 3. 見積もれない・外れたときだけ二分探索する
 */

/** 計測を後回しにする上限（この時間内にアイドルにならなければ実行する） */
const MEASURE_IDLE_TIMEOUT_MS = 300;

let segmenter: Intl.Segmenter | null | undefined;

/**
 * 書記素クラスタの境界（先頭 0 と末尾 text.length を含む）。
 * 省略位置をここに揃え、絵文字（サロゲートペア・ZWJ 連結）を途中で切って「�」を出さないようにする。
 */
export function graphemeBoundaries(text: string): number[] {
  if (segmenter === undefined) {
    segmenter =
      typeof Intl !== "undefined" && typeof Intl.Segmenter === "function"
        ? new Intl.Segmenter("ja", { granularity: "grapheme" })
        : null;
  }
  const bounds = [0];
  if (segmenter) {
    for (const { index, segment } of segmenter.segment(text)) bounds.push(index + segment.length);
  } else {
    // Intl.Segmenter の無い古いブラウザ: 少なくともサロゲートペアは分割しない
    let offset = 0;
    for (const char of text) {
      offset += char.length;
      bounds.push(offset);
    }
  }
  return bounds;
}

/**
 * prefixFits(i)（先頭 i 書記素 + 「… 続きを読む」が収まるか。i について単調）が true になる最大の i を
 * 0〜maxIndex から探す。どれも収まらなければ 0。
 * estimate があれば、その位置と 1 つ先だけを確かめる（外れていたら残りの範囲を二分探索する）。
 */
export function findCaptionCut(
  maxIndex: number,
  estimate: number | null,
  prefixFits: (index: number) => boolean,
): number {
  if (maxIndex <= 0) return 0;
  let lo = 0;
  let hi = maxIndex;
  if (estimate !== null) {
    const guess = Math.min(Math.max(Math.round(estimate), 0), maxIndex);
    if (!prefixFits(guess)) {
      hi = guess - 1;
    } else if (guess < maxIndex && prefixFits(guess + 1)) {
      lo = guess + 1;
    } else {
      return guess;
    }
  }
  while (lo < hi) {
    const mid = Math.ceil((lo + hi) / 2);
    if (prefixFits(mid)) lo = mid;
    else hi = mid - 1;
  }
  return Math.max(lo, 0);
}

export interface CaptionLayout {
  /** 計測用要素の矩形（内側の余白なし） */
  box: DOMRect;
  lineHeight: number;
  maxLines: number;
  /** 「… 続きを読む」の幅（折り返さない状態） */
  moreWidth: number;
}

/**
 * 全文を流し込んだ計測用要素のレイアウトから、「… 続きを読む」を最終行に置ける書記素数を見積もる。
 * DOM を書き換えずに読むだけなので、追加の強制レイアウトは発生しない。見積もれなければ null。
 */
export function estimateCaptionCut(
  textNode: Text,
  bounds: readonly number[],
  { box, lineHeight, maxLines, moreWidth }: CaptionLayout,
): number | null {
  const count = bounds.length - 1;
  if (count <= 0 || lineHeight <= 0) return null;
  const range = document.createRange();
  const rectOf = (i: number): DOMRect | null => {
    range.setStart(textNode, bounds[i] ?? 0);
    range.setEnd(textNode, bounds[i + 1] ?? 0);
    const rects = range.getClientRects();
    for (let k = rects.length - 1; k >= 0; k -= 1) {
      const rect = rects[k];
      if (rect && rect.height > 0) return rect;
    }
    return null; // 改行文字など
  };
  const lineOfRect = (rect: DOMRect) =>
    Math.floor((rect.top + rect.height / 2 - box.top) / lineHeight);
  /** i 番目の書記素の行（0 始まり）。測れない文字は直前の文字の行とみなす */
  const lineAt = (i: number): number | null => {
    for (let j = i; j >= Math.max(0, i - 3); j -= 1) {
      const rect = rectOf(j);
      if (rect) return lineOfRect(rect);
    }
    return null;
  };

  // 最終行（maxLines 行目）までに入っている最後の書記素（行番号は単調増加なので二分探索）
  const firstLine = lineAt(0);
  if (firstLine === null) return null;
  if (firstLine >= maxLines) return 0;
  let lo = 0;
  let hi = count - 1;
  while (lo < hi) {
    const mid = Math.ceil((lo + hi) / 2);
    const line = lineAt(mid);
    if (line === null) return null;
    if (line < maxLines) lo = mid;
    else hi = mid - 1;
  }

  // 最終行の右端に「… 続きを読む」が収まるところまで戻る
  let cut = lo + 1;
  while (cut > 0) {
    const rect = rectOf(cut - 1);
    if (rect && (lineOfRect(rect) < maxLines - 1 || rect.right + moreWidth <= box.right)) break;
    cut -= 1;
  }
  return cut;
}

/**
 * 描画の後（アイドル時）に実行する。キャンセル関数を返す。
 * 計測を最初の描画・画像の取得開始・スクロールの妨げにしないよう後回しにする。
 */
export function scheduleAfterPaint(callback: () => void): () => void {
  if (typeof window.requestIdleCallback === "function") {
    const id = window.requestIdleCallback(callback, { timeout: MEASURE_IDLE_TIMEOUT_MS });
    return () => window.cancelIdleCallback(id);
  }
  // Safari（requestIdleCallback 未対応）: 次の描画が終わってから実行する
  let timer: ReturnType<typeof setTimeout> | undefined;
  const frame = requestAnimationFrame(() => {
    timer = setTimeout(callback, 0);
  });
  return () => {
    cancelAnimationFrame(frame);
    clearTimeout(timer);
  };
}
