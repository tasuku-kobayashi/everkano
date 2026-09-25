import { describe, expect, it } from "vitest";
import {
  anchorShift,
  correctionFor,
  findFirstVisibleRow,
  type ScrollAnchor,
} from "./use-chat-scroll";

/** 高さ h の行を上から順に並べたときの [top, bottom]（document 座標） */
function layout(keys: readonly string[], h = 44, offset = 0): Map<string, number> {
  return new Map(keys.map((key, index) => [key, offset + index * h]));
}

describe("findFirstVisibleRow", () => {
  const bottoms = [40, 80, 120, 160, 200];
  const at = (index: number) => bottoms[index]!;

  it("下端が viewportTop より下にある最初の行", () => {
    expect(findFirstVisibleRow(bottoms.length, at, 0)).toBe(0);
    expect(findFirstVisibleRow(bottoms.length, at, 80)).toBe(2); // 下端ちょうどは見えていない
    expect(findFirstVisibleRow(bottoms.length, at, 81)).toBe(2);
    expect(findFirstVisibleRow(bottoms.length, at, 199)).toBe(4);
  });

  it("すべて上に隠れていれば count、行が無ければ 0", () => {
    expect(findFirstVisibleRow(bottoms.length, at, 500)).toBe(5);
    expect(findFirstVisibleRow(0, at, 0)).toBe(0);
  });
});

describe("anchorShift（見ている位置の補正量）", () => {
  it("過去ログを先頭に足したら、表示中の行が下がった分だけ補正する", () => {
    const before = layout(["m40", "m41", "m42"], 44, 1000);
    const anchors: ScrollAnchor[] = ["m41", "m42"].map((key) => ({ key, top: before.get(key)! }));
    const after = layout(["o1", "o2", "o3", "m40", "m41", "m42"], 44, 1000);
    expect(anchorShift(anchors, (key) => after.get(key) ?? null)).toBe(3 * 44);
  });

  it("再取得で一番古い行が消えても、残っている表示中の行を基準に補正する", () => {
    // 100 件中 2 ページ（+ 新着 10 件）を読み込み、#45 付近を読んでいるときに古い 10 件が落ちた
    const keys = Array.from({ length: 70 }, (_, i) => `m${i}`);
    const before = layout(keys, 44);
    const anchors: ScrollAnchor[] = keys
      .slice(45, 53)
      .map((key) => ({ key, top: before.get(key)! }));
    const after = layout(keys.slice(10), 44);
    expect(anchorShift(anchors, (key) => after.get(key) ?? null)).toBe(-10 * 44);
  });

  it("先頭の候補が消えていたら次の候補を使い、すべて消えていたら null", () => {
    const anchors: ScrollAnchor[] = [
      { key: "gone", top: 100 },
      { key: "kept", top: 200 },
    ];
    expect(anchorShift(anchors, (key) => (key === "kept" ? 150 : null))).toBe(-50);
    expect(anchorShift(anchors, () => null)).toBeNull();
    expect(anchorShift([], () => 0)).toBeNull();
  });
});

describe("correctionFor", () => {
  it("途中を読んでいるときは基準行の移動量そのまま", () => {
    expect(correctionFor(-440, { captured: 2000, current: 2000, max: 5000 })).toBe(-440);
    expect(correctionFor(132, { captured: 2000, current: 2000, max: 5000 })).toBe(132);
  });

  it("最下部で上の行が減って文書が縮んだら、ブラウザが切り詰めた分を差し引く（最下部のまま）", () => {
    // 切り詰め済み（scrollY 5000 → 4560）: もう動かさない
    expect(correctionFor(-440, { captured: 5000, current: 4560, max: 4560 })).toBe(0);
    // 切り詰めがまだ反映されていない: 移動量どおり動かせば新しい最下部になる
    expect(correctionFor(-440, { captured: 5000, current: 5000, max: 4560 })).toBe(-440);
  });

  it("最下部の少し上にいたときは、見ていた行が同じ位置に来るように残りを補正する", () => {
    // 最下部の 100px 上（4900）→ 440px 縮んで最下部 4560 に切り詰め → さらに 100px 上へ
    expect(correctionFor(-440, { captured: 4900, current: 4560, max: 4560 })).toBe(-100);
  });
});
