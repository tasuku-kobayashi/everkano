import { describe, expect, it } from "vitest";
import { codePointLength, firstGrapheme } from "./text";

describe("firstGrapheme", () => {
  it("絵文字で始まる名前でもサロゲートペアを分割しない", () => {
    expect(firstGrapheme("🌸さくら")).toBe("🌸");
    expect(firstGrapheme("😀")).toBe("😀");
  });

  it("ZWJ で結合された絵文字・国旗・結合文字も 1 文字として扱う", () => {
    expect(firstGrapheme("👨‍👩‍👧家族アカウント")).toBe("👨‍👩‍👧");
    expect(firstGrapheme("🇯🇵japan")).toBe("🇯🇵");
    expect(firstGrapheme("が")).toBe("が"); // か + 濁点（結合文字）
  });

  it("通常の文字・空白・空文字", () => {
    expect(firstGrapheme("さくら")).toBe("さ");
    expect(firstGrapheme("  misaki")).toBe("m");
    expect(firstGrapheme("")).toBe("");
    expect(firstGrapheme("   ")).toBe("");
  });

  it("結果に孤立したサロゲートを含まない", () => {
    for (const name of ["🌸さくら", "👨‍👩‍👧家族", "𠮷野家"]) {
      expect(firstGrapheme(name)).not.toMatch(/^[\uD800-\uDBFF]$|^[\uDC00-\uDFFF]$/);
    }
  });
});

describe("codePointLength", () => {
  it("絵文字 1 つを 1 と数える（DB の char_length と同じ）", () => {
    expect(codePointLength("🌸さくら")).toBe(4);
    expect("🌸さくら".length).toBe(5);
    expect(codePointLength("")).toBe(0);
    expect(codePointLength("𠮷")).toBe(1);
  });
});
