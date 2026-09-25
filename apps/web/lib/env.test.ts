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
    });
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
  });
});
