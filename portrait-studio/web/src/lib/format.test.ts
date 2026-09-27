import { describe, expect, it } from "vitest";
import { dayEndIso, dayStartIso, gradeSimilarity, isoToLocalDateInput, lowerResolution, pct, similarityLabel } from "./format";

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

describe("date filter bounds", () => {
  it("covers the whole local day and round-trips to the picked date", () => {
    const start = new Date(dayStartIso("2026-09-27"));
    const end = new Date(dayEndIso("2026-09-27"));
    expect(start.getHours()).toBe(0);
    expect(start.getMinutes()).toBe(0);
    expect(end.getHours()).toBe(23);
    expect(end.getTime() - start.getTime()).toBe(24 * 60 * 60 * 1000 - 1);
    expect(dayStartIso("2026-09-27").endsWith("Z")).toBe(true);
    expect(isoToLocalDateInput(dayStartIso("2026-09-27"))).toBe("2026-09-27");
    expect(isoToLocalDateInput(dayEndIso("2026-09-27"))).toBe("2026-09-27");
    expect(isoToLocalDateInput(undefined)).toBe("");
    expect(isoToLocalDateInput("garbage")).toBe("");
  });
});
