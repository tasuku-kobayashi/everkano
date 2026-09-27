import { describe, expect, it } from "vitest";
import { composePrompt, parseTags } from "./prompt";

describe("composePrompt", () => {
  it("joins prefix, scene fragments and the user prompt", () => {
    const scene = { id: "cafe", name: "カフェ", fragments: ["at a cafe", " window light "], description: "", builtin: true, verify: false };
    expect(composePrompt("photo, japanese", [scene], " smiling ")).toBe("photo, japanese, at a cafe, window light, smiling");
    expect(composePrompt("photo", [], "")).toBe("photo");
  });
});

describe("parseTags", () => {
  it("splits on comma / whitespace / 読点 and dedupes", () => {
    expect(parseTags("a, b、c  a")).toEqual(["a", "b", "c"]);
  });
});
