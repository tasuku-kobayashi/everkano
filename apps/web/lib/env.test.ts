import { describe, expect, it } from "vitest";
import { EnvValidationError, parsePublicEnv } from "./env";

const valid = {
  NEXT_PUBLIC_SUPABASE_URL: "http://127.0.0.1:54321/",
  NEXT_PUBLIC_SUPABASE_ANON_KEY: "anon",
  NEXT_PUBLIC_API_BASE_URL: "http://localhost:8000/",
};

describe("parsePublicEnv", () => {
  it("既定値を補い、末尾スラッシュを除く", () => {
    const env = parsePublicEnv({ ...valid, NEXT_PUBLIC_SITE_URL: "" });
    expect(env).toEqual({
      supabaseUrl: "http://127.0.0.1:54321",
      supabaseAnonKey: "anon",
      apiBaseUrl: "http://localhost:8000",
      siteUrl: undefined,
      storageDriver: "passthrough",
      cdnBaseUrl: undefined,
      mediaSigned: false,
      enableServiceWorker: false,
      buildId: "dev",
    });
  });

  it("ビルド ID（Service Worker の登録 URL 用）は英数字・_・- だけにする", () => {
    expect(parsePublicEnv({ ...valid, NEXT_PUBLIC_BUILD_ID: "dpl_AbC-123" }).buildId).toBe(
      "dpl_AbC-123",
    );
    expect(parsePublicEnv({ ...valid, NEXT_PUBLIC_BUILD_ID: "a/b?c=<d>" }).buildId).toBe("abcd");
    expect(parsePublicEnv({ ...valid, NEXT_PUBLIC_BUILD_ID: "  " }).buildId).toBe("dev");
    expect(parsePublicEnv({ ...valid, NEXT_PUBLIC_BUILD_ID: "///" }).buildId).toBe("dev");
  });

  it("未設定の必須値は分かりやすいメッセージで失敗する", () => {
    expect(() => parsePublicEnv({})).toThrow(EnvValidationError);
    try {
      parsePublicEnv({ ...valid, NEXT_PUBLIC_SUPABASE_ANON_KEY: "" });
    } catch (error) {
      expect(String((error as Error).message)).toContain("NEXT_PUBLIC_SUPABASE_ANON_KEY");
      expect(String((error as Error).message)).toContain(".env.local");
    }
  });

  it("bunny ドライバーは CDN URL 必須", () => {
    expect(() => parsePublicEnv({ ...valid, NEXT_PUBLIC_STORAGE_DRIVER: "bunny" })).toThrow(
      /NEXT_PUBLIC_CDN_BASE_URL/,
    );
    const env = parsePublicEnv({
      ...valid,
      NEXT_PUBLIC_STORAGE_DRIVER: "bunny",
      NEXT_PUBLIC_CDN_BASE_URL: "https://z.b-cdn.net/",
      NEXT_PUBLIC_MEDIA_SIGNED: "1",
    });
    expect(env.storageDriver).toBe("bunny");
    expect(env.cdnBaseUrl).toBe("https://z.b-cdn.net");
    expect(env.mediaSigned).toBe(true);
  });

  it("不正なドライバー名・URL を拒否", () => {
    expect(() => parsePublicEnv({ ...valid, NEXT_PUBLIC_STORAGE_DRIVER: "s3" })).toThrow(
      EnvValidationError,
    );
    expect(() => parsePublicEnv({ ...valid, NEXT_PUBLIC_API_BASE_URL: "localhost" })).toThrow(
      /NEXT_PUBLIC_API_BASE_URL/,
    );
    // スキームが http(s) 以外・ホストなし
    expect(() => parsePublicEnv({ ...valid, NEXT_PUBLIC_API_BASE_URL: "localhost:8000" })).toThrow(
      /NEXT_PUBLIC_API_BASE_URL/,
    );
    expect(() =>
      parsePublicEnv({ ...valid, NEXT_PUBLIC_SUPABASE_URL: "javascript:alert(1)" }),
    ).toThrow(/NEXT_PUBLIC_SUPABASE_URL/);
    expect(() =>
      parsePublicEnv({
        ...valid,
        NEXT_PUBLIC_STORAGE_DRIVER: "bunny",
        NEXT_PUBLIC_CDN_BASE_URL: "not a url",
      }),
    ).toThrow(/NEXT_PUBLIC_CDN_BASE_URL/);
    expect(() => parsePublicEnv({ ...valid, NEXT_PUBLIC_ENABLE_SW: "yes" })).toThrow(
      /NEXT_PUBLIC_ENABLE_SW/,
    );
  });

  it("問題のあるキーをすべて 1 回のエラーで報告する", () => {
    try {
      parsePublicEnv({});
      expect.unreachable();
    } catch (error) {
      const message = String((error as Error).message);
      expect(message).toContain("NEXT_PUBLIC_SUPABASE_URL");
      expect(message).toContain("NEXT_PUBLIC_SUPABASE_ANON_KEY");
      expect(message).toContain("NEXT_PUBLIC_API_BASE_URL");
    }
  });

  it("前後の空白は無視し、空白のみは未設定として扱う", () => {
    const env = parsePublicEnv({
      ...valid,
      NEXT_PUBLIC_SITE_URL: "  https://everkano.example/  ",
      NEXT_PUBLIC_STORAGE_DRIVER: " ",
      NEXT_PUBLIC_ENABLE_SW: "1",
    });
    expect(env.siteUrl).toBe("https://everkano.example");
    expect(env.storageDriver).toBe("passthrough");
    expect(env.enableServiceWorker).toBe(true);
  });
});

describe("クライアントバンドル", () => {
  it("lib/env.ts は zod を import しない（全画面の初回 JS に入るため）", async () => {
    const { readFile } = await import("node:fs/promises");
    const source = await readFile(new URL("./env.ts", import.meta.url), "utf8");
    expect(source).not.toMatch(/from\s+["']zod["']/);
  });
});
