import { describe, expect, it } from "vitest";
import { gradeSimilarity, lowerResolution, pct, similarityLabel } from "./format";

describe("gradeSimilarity", () => {
  const t = { good: 0.75, acceptable: 0.6 };
  it("maps thresholds", () => {
    expect(gradeSimilarity(0.8, t)).toBe("good");
    expect(gradeSimilarity(0.75, t)).toBe("good");
    expect(gradeSimilarity(0.65, t)).toBe("acceptable");
    expect(gradeSimilarity(0.59, t)).toBe("warning");
    expect(gradeSimilarity(null, t)).toBe("unknown");
  });
  it("labels null as no-face", () => {
    expect(similarityLabel(null)).toBe("顔検出不可");
    expect(similarityLabel(0.812)).toBe("0.81");
  });
});

describe("lowerResolution", () => {
  it("steps down known presets and keeps multiples of 8", () => {
    expect(lowerResolution(832, 1216)).toEqual([768, 1152]);
    expect(lowerResolution(640, 960)).toEqual([576, 864]);
    const [w, h] = lowerResolution(1000, 1000);
    expect(w % 8).toBe(0);
    expect(h % 8).toBe(0);
  });
});

describe("pct", () => {
  it("clamps", () => {
    expect(pct(50, 100)).toBe(50);
    expect(pct(500, 100)).toBe(100);
    expect(pct(1, 0)).toBe(0);
  });
});
