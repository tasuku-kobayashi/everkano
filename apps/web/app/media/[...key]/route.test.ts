import { NextRequest } from "next/server";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { computeExpires, signBunnyUrl } from "@/lib/storage/bunny-token";

/**
 * GET /media/<key>（Bunny Token Authentication の署名 URL を発行する画像プロキシ）のテスト。
 * 画像の拡張子を持つパスは middleware を通らないため、この Route Handler 自身のセッション確認が唯一の関門。
 */

const serverEnv = {
  bunnyTokenAuthKey: "secret-key-123" as string | undefined,
  bunnyTokenTtlSeconds: 3600,
};
const publicEnv = { cdnBaseUrl: "https://cdn.example.net" as string | undefined };
const getClaims = vi.fn();

vi.mock("@/lib/env.server", () => ({ getServerEnv: () => serverEnv }));
vi.mock("@/lib/env", () => ({ getPublicEnv: () => publicEnv }));
vi.mock("@/lib/supabase/server", () => ({
  createSupabaseServerClient: async () => ({ auth: { getClaims } }),
}));

const { GET } = await import("./route");

const NOW = Date.parse("2026-09-25T12:00:00Z");

function call(path: string, key: string[]) {
  return GET(new NextRequest(new URL(path, "https://app.example")), {
    params: Promise.resolve({ key }),
  });
}

describe("GET /media/[...key]", () => {
  beforeEach(() => {
    serverEnv.bunnyTokenAuthKey = "secret-key-123";
    serverEnv.bunnyTokenTtlSeconds = 3600;
    publicEnv.cdnBaseUrl = "https://cdn.example.net";
    getClaims.mockReset();
    getClaims.mockResolvedValue({ data: { claims: { sub: "user-1" } }, error: null });
    vi.useFakeTimers({ toFake: ["Date"] });
    vi.setSystemTime(NOW);
  });

  afterEach(() => {
    vi.useRealTimers();
  });

  it("署名キーが未設定なら 404（署名 URL を使わない構成）", async () => {
    serverEnv.bunnyTokenAuthKey = undefined;
    const res = await call("/media/posts/a.jpg", ["posts", "a.jpg"]);
    expect(res.status).toBe(404);
    expect(getClaims).not.toHaveBeenCalled();
  });

  it("CDN の URL が未設定なら 500（設定ミス）", async () => {
    publicEnv.cdnBaseUrl = undefined;
    const error = vi.spyOn(console, "error").mockImplementation(() => undefined);
    const res = await call("/media/posts/a.jpg", ["posts", "a.jpg"]);
    expect(res.status).toBe(500);
    error.mockRestore();
  });

  it("未ログイン（セッションなし・JWT 不正）は 401 で、署名 URL を返さない", async () => {
    getClaims.mockResolvedValue({ data: null, error: { name: "AuthSessionMissingError" } });
    const res = await call("/media/posts/a.jpg", ["posts", "a.jpg"]);
    expect(res.status).toBe(401);
    expect(res.headers.get("location")).toBeNull();
  });

  it("ログイン済みなら、変換パラメータを含めて署名した CDN の URL へ 302", async () => {
    const res = await call("/media/posts/a%20b.jpg?width=640&quality=80&blur=0&token=x&evil=1", [
      "posts",
      "a b.jpg",
    ]);
    expect(res.status).toBe(302);
    const expected = signBunnyUrl({
      cdnBaseUrl: "https://cdn.example.net",
      path: "/posts/a%20b.jpg",
      securityKey: "secret-key-123",
      expires: computeExpires(NOW, 3600),
      params: [
        ["quality", "80"],
        ["width", "640"],
      ],
    });
    expect(res.headers.get("location")).toBe(expected);
    // 受け付けないパラメータ（token・未知のキー）は署名にも URL にも含めない
    expect(res.headers.get("location")).not.toContain("evil");
    expect(res.headers.get("location")?.match(/token=/g)).toHaveLength(1);
    // 署名 URL が失効する前に使い終わるよう、ブラウザのキャッシュは短く・private
    expect(res.headers.get("cache-control")).toBe("private, max-age=300");
  });

  it("数値でない変換パラメータは無視する", async () => {
    const res = await call("/media/a.jpg?width=abc&quality=-1", ["a.jpg"]);
    const location = res.headers.get("location") ?? "";
    expect(location).not.toContain("width=");
    expect(location).not.toContain("quality=");
  });

  it("パストラバーサル（.. / .）のキーは 400", async () => {
    const res = await call("/media/posts/../secret.jpg", ["posts", "..", "secret.jpg"]);
    expect(res.status).toBe(400);
  });
});
