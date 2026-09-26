import { describe, expect, it } from "vitest";
import {
  characterStateKey,
  DEFAULT_STATUS_LABEL,
  showsActiveDot,
  statusLine,
} from "./character-state";
import { queryKeys } from "./keys";

describe("DM ヘッダーの今の状況", () => {
  it("status_label を出し、無ければ「アクティブ」", () => {
    expect(DEFAULT_STATUS_LABEL).toBe("アクティブ");
    expect(statusLine({ status_label: "仕事中" })).toBe("仕事中");
    expect(statusLine({ status_label: "  カフェで\nひと休み " })).toBe("カフェで ひと休み");
    expect(statusLine({ status_label: null })).toBe("アクティブ");
    expect(statusLine({ status_label: "   " })).toBe("アクティブ");
    expect(statusLine(null)).toBe("アクティブ");
    expect(statusLine(undefined)).toBe("アクティブ");
  });

  it("忙しい（busyness 2 以上）ときは緑の点を出さない。状況が分からなければ出す", () => {
    expect(showsActiveDot({ busyness: 0 })).toBe(true);
    expect(showsActiveDot({ busyness: 1 })).toBe(true);
    expect(showsActiveDot({ busyness: 2 })).toBe(false);
    expect(showsActiveDot({ busyness: 3 })).toBe(false);
    expect(showsActiveDot(null)).toBe(true);
  });

  it("クエリキーは DM ヘッダー用のキャラ情報（characterById の派生）と衝突しない", () => {
    expect(characterStateKey("c1")).toEqual([...queryKeys.characterById("c1"), "state"]);
  });
});
