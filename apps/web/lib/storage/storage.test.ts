import { createHash } from "node:crypto";
import { describe, expect, it } from "vitest";
import { buildTransformParams, createBunnyAdapter, encodeObjectKey } from "./bunny";
import { computeBunnyToken, computeExpires, signBunnyUrl, toBunnyBase64Url } from "./bunny-token";
import { buildSrcSet } from "./index";
import { createPassthroughAdapter } from "./passthrough";
import { isAbsoluteUrl } from "./types";

describe("isAbsoluteUrl", () => {
  it("http(s) / プロトコル相対 / data / blob を判定", () => {
    expect(isAbsoluteUrl("https://picsum.photos/seed/a/1080")).toBe(true);
    expect(isAbsoluteUrl("http://example.com/a.jpg")).toBe(true);
    expect(isAbsoluteUrl("//cdn.example.com/a.jpg")).toBe(true);
    expect(isAbsoluteUrl("data:image/png;base64,AAA")).toBe(true);
    expect(isAbsoluteUrl("blob:https://x/1")).toBe(true);
    expect(isAbsoluteUrl("posts/a.jpg")).toBe(false);
    expect(isAbsoluteUrl("/posts/a.jpg")).toBe(false);
  });
});

describe("passthrough", () => {
  it("値をそのまま返し、変換は非対応", () => {
    const adapter = createPassthroughAdapter();
    expect(adapter.resolveImageUrl("https://picsum.photos/seed/x/1080", { width: 320 })).toBe(
      "https://picsum.photos/seed/x/1080",
    );
    expect(adapter.supportsTransforms).toBe(false);
    expect(buildSrcSet("https://a/b.jpg", [320, 640], {}, adapter)).toBeUndefined();
  });
});

describe("bunny（CDN 直）", () => {
  const adapter = createBunnyAdapter({
    cdnBaseUrl: "https://everkano.b-cdn.net/",
    mediaSigned: false,
  });

  it("オブジェクトキーを CDN URL + Optimizer パラメータ（キー昇順）に変換", () => {
    expect(adapter.resolveImageUrl("posts/misaki/1.jpg")).toBe(
      "https://everkano.b-cdn.net/posts/misaki/1.jpg",
    );
    expect(adapter.resolveImageUrl("/posts/misaki/1.jpg", { width: 640, quality: 80 })).toBe(
      "https://everkano.b-cdn.net/posts/misaki/1.jpg?quality=80&width=640",
    );
    expect(adapter.resolveImageUrl("posts/p.jpg", { width: 320, blur: 40 })).toBe(
      "https://everkano.b-cdn.net/posts/p.jpg?blur=40&width=320",
    );
  });

  it("絶対URLは変換しない", () => {
    const url = "https://api.dicebear.com/9.x/notionists/svg?seed=a";
    expect(adapter.resolveImageUrl(url, { width: 320 })).toBe(url);
  });

  it("パスセグメントをエンコードし、.. を拒否する", () => {
    expect(encodeObjectKey("a b/ねこ.jpg")).toBe("/a%20b/%E3%81%AD%E3%81%93.jpg");
    expect(() => encodeObjectKey("posts/../secret.jpg")).toThrow();
  });

  it("パラメータを範囲内に丸める", () => {
    expect(buildTransformParams({ width: 99999, quality: 0, blur: 150 })).toEqual([
      ["blur", "100"],
      ["quality", "1"],
      ["width", "4096"],
    ]);
    expect(buildTransformParams({ blur: 0 })).toEqual([]);
  });

  it("srcSet を幅ごとに作る", () => {
    expect(buildSrcSet("posts/a.jpg", [320, 640], { quality: 80 }, adapter)).toBe(
      "https://everkano.b-cdn.net/posts/a.jpg?quality=80&width=320 320w, " +
        "https://everkano.b-cdn.net/posts/a.jpg?quality=80&width=640 640w",
    );
  });
});

describe("bunny（トークン認証 = /media 経由）", () => {
  it("同一オリジンの /media ルートを返す（署名キーはクライアントに渡らない）", () => {
    const adapter = createBunnyAdapter({ cdnBaseUrl: "https://z.b-cdn.net", mediaSigned: true });
    expect(adapter.resolveImageUrl("posts/a.jpg", { width: 640 })).toBe(
      "/media/posts/a.jpg?width=640",
    );
    expect(adapter.resolveImageUrl("https://x.test/a.jpg")).toBe("https://x.test/a.jpg");
  });
});

describe("bunny-token（サーバー専用の署名）", () => {
  const key = "test-security-key-123";

  it("SHA256(key + path + expires + params) の URL-safe Base64", () => {
    const expected = createHash("sha256")
      .update(`${key}/posts/a.jpg1790000000quality=80&width=640`)
      .digest("base64")
      .replace(/\+/g, "-")
      .replace(/\//g, "_")
      .replace(/=/g, "");
    const token = computeBunnyToken({
      path: "/posts/a.jpg",
      securityKey: key,
      expires: 1_790_000_000,
      params: [
        ["width", "640"],
        ["quality", "80"],
      ],
    });
    expect(token).toBe(expected);
    expect(token).not.toMatch(/[+/=]/);
  });

  it("既知ベクトル（回帰テスト）", () => {
    expect(
      computeBunnyToken({ path: "/posts/a.jpg", securityKey: key, expires: 1_790_000_000 }),
    ).toBe("Sltjj1AYFHLOQGKxlWK1-cw4u_OPxPs1w-MTQgRD5rs");
  });

  it("署名URLの形式: path?token=..&<params>&expires=..", () => {
    const url = signBunnyUrl({
      cdnBaseUrl: "https://z.b-cdn.net/",
      path: "/posts/a.jpg",
      securityKey: key,
      expires: 1_790_000_000,
      params: [["width", "640"]],
    });
    expect(url).toMatch(
      /^https:\/\/z\.b-cdn\.net\/posts\/a\.jpg\?token=[A-Za-z0-9_-]{43}&width=640&expires=1790000000$/,
    );
  });

  it("toBunnyBase64Url", () => {
    expect(toBunnyBase64Url(Buffer.from([0xfb, 0xff, 0xfe]))).toBe("-__-");
  });

  it("失効時刻は ttl 以上・5分単位", () => {
    const now = Date.parse("2026-09-25T00:00:10Z");
    const expires = computeExpires(now, 3600);
    expect(expires % 300).toBe(0);
    expect(expires).toBeGreaterThanOrEqual(Math.floor(now / 1000) + 3600);
    expect(expires).toBeLessThan(Math.floor(now / 1000) + 3600 + 300);
  });
});
