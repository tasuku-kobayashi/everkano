import { describe, expect, it } from "vitest";
import { findCaptionCut, graphemeBoundaries } from "./caption-measure";

describe("graphemeBoundaries", () => {
  it("書記素クラスタの境界（先頭 0 と末尾を含む）。絵文字を分割しない", () => {
    expect(graphemeBoundaries("あいう")).toEqual([0, 1, 2, 3]);
    // 🍋 はサロゲートペア（2 コード単位）、👨‍👩‍👧 は ZWJ 連結（8 コード単位）で 1 書記素
    expect(graphemeBoundaries("a🍋👨‍👩‍👧b")).toEqual([0, 1, 3, 11, 12]);
    expect(graphemeBoundaries("")).toEqual([0]);
  });
});

/** 先頭 i 書記素が収まるかを数えながら返す偽の計測（limit 以下なら収まる） */
function probe(limit: number) {
  const calls: number[] = [];
  return {
    calls,
    fits: (i: number) => {
      calls.push(i);
      return i <= limit;
    },
  };
}

describe("findCaptionCut", () => {
  it("見積もりが正しければ 2 回の計測（その位置と 1 つ先）で確定する", () => {
    const p = probe(37);
    expect(findCaptionCut(80, 37, p.fits)).toBe(37);
    expect(p.calls).toEqual([37, 38]);
  });

  it("見積もりが控えめすぎても・大きすぎても正しい最大値を返す", () => {
    for (const estimate of [0, 5, 36, 38, 60, 80, 500, -3]) {
      expect(findCaptionCut(80, estimate, probe(37).fits)).toBe(37);
    }
  });

  it("見積もりが無ければ二分探索（従来どおり）", () => {
    const p = probe(37);
    expect(findCaptionCut(80, null, p.fits)).toBe(37);
    expect(p.calls.length).toBeLessThanOrEqual(8);
  });

  it("どこまでも収まらなければ 0、上限まで収まれば上限", () => {
    expect(findCaptionCut(80, 10, probe(-1).fits)).toBe(0);
    expect(findCaptionCut(80, null, probe(-1).fits)).toBe(0);
    expect(findCaptionCut(80, 79, probe(1000).fits)).toBe(80);
    expect(findCaptionCut(0, 3, probe(1000).fits)).toBe(0);
  });
});
