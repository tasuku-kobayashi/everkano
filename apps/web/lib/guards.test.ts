import { describe, expect, it } from "vitest";
import { isRecord, isUuid } from "./guards";

describe("isRecord", () => {
  it("プレーンなオブジェクトだけ（null・配列・プリミティブは除く）", () => {
    expect(isRecord({})).toBe(true);
    expect(isRecord({ a: 1 })).toBe(true);
    expect(isRecord(null)).toBe(false);
    expect(isRecord([])).toBe(false);
    expect(isRecord("x")).toBe(false);
    expect(isRecord(1)).toBe(false);
    expect(isRecord(undefined)).toBe(false);
  });
});

describe("isUuid", () => {
  it("UUID 形式（大文字も可）だけ", () => {
    expect(isUuid("00000000-0000-4000-8000-000000000c10")).toBe(true);
    expect(isUuid("00000000-0000-4000-8000-000000000C10")).toBe(true);
    expect(isUuid("not-a-uuid")).toBe(false);
    expect(isUuid("")).toBe(false);
    expect(isUuid("00000000-0000-4000-8000-000000000c10x")).toBe(false);
  });
});
