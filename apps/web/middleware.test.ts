import { unstable_doesMiddlewareMatch } from "next/experimental/testing/server";
import { NextRequest, NextResponse } from "next/server";
import { beforeEach, describe, expect, it, vi } from "vitest";
import type * as SupabaseMiddleware from "@/lib/supabase/middleware";

/**
 * middleware.ts の結合テスト（updateSession だけを差し替え、リダイレクト・401・Cookie の引き継ぎを確認する）。
 * 判定の細かい組み合わせは lib/auth/route-gate.test.ts。
 */

const updateSession = vi.fn();
vi.mock("@/lib/supabase/middleware", async (importOriginal) => ({
  ...(await importOriginal<typeof SupabaseMiddleware>()),
  updateSession: (request: NextRequest) => updateSession(request),
}));

const { config, middleware } = await import("./middleware");

const ORIGIN = "https://app.example";
const USER = "11111111-1111-4111-8111-111111111111";

/** updateSession の結果（リフレッシュ済みの Cookie を持つレスポンス） */
function session(userId: string | null, refreshedCookie?: string) {
  const response = NextResponse.next();
  if (refreshedCookie) response.cookies.set("sb-test-auth-token", refreshedCookie);
  return { response, userId };
}

function request(path: string) {
  return new NextRequest(new URL(path, ORIGIN));
}

describe("middleware", () => {
  beforeEach(() => {
    updateSession.mockReset();
  });

  it("未ログインで保護ページ → /login?next=（307）", async () => {
    updateSession.mockResolvedValue(session(null));
    const res = await middleware(request("/dm/abc?x=1"));
    expect(res.status).toBe(307);
    expect(res.headers.get("location")).toBe(
      `${ORIGIN}/login?next=${encodeURIComponent("/dm/abc?x=1")}`,
    );
  });

  it("未ログインで /media → 本文なしの 401（署名 URL を発行しない）", async () => {
    updateSession.mockResolvedValue(session(null));
    const res = await middleware(request("/media/posts/a.jpg?width=640"));
    expect(res.status).toBe(401);
    expect(res.headers.get("location")).toBeNull();
  });

  it("公開パス（ログイン画面・マジックリンクの着地点）はそのまま通す", async () => {
    updateSession.mockResolvedValue(session(null));
    for (const path of ["/login", "/auth/confirm?token_hash=abcdefgh&type=email", "/offline"]) {
      const res = await middleware(request(path));
      expect(res.headers.get("location"), path).toBeNull();
      expect(res.headers.get("x-middleware-next"), path).toBe("1");
    }
  });

  it("ログイン済みで /login → /。リフレッシュしたセッション Cookie をリダイレクトにも引き継ぐ", async () => {
    updateSession.mockResolvedValue(session(USER, "refreshed"));
    const res = await middleware(request("/login"));
    expect(res.status).toBe(307);
    expect(res.headers.get("location")).toBe(`${ORIGIN}/`);
    expect(res.cookies.get("sb-test-auth-token")?.value).toBe("refreshed");
  });

  it("ログイン済みで /login?next= → 開こうとしていたページ（同一オリジンの相対パスだけ）", async () => {
    updateSession.mockResolvedValue(session(USER));
    const res = await middleware(
      request(`/login?next=${encodeURIComponent("/posts/p1?comment=c1")}`),
    );
    expect(res.headers.get("location")).toBe(`${ORIGIN}/posts/p1?comment=c1`);
    const evil = await middleware(request(`/login?next=${encodeURIComponent("//evil.example")}`));
    expect(evil.headers.get("location")).toBe(`${ORIGIN}/`);
  });

  it("ログイン済みでも ?error=withdrawn / session / banned のログイン画面は表示する（ループ防止）", async () => {
    updateSession.mockResolvedValue(session(USER));
    for (const reason of ["withdrawn", "session", "banned"]) {
      const res = await middleware(request(`/login?error=${reason}`));
      expect(res.headers.get("location"), reason).toBeNull();
    }
  });

  it("ログイン済みで保護ページはそのまま通す（Cookie の更新を含むレスポンス）", async () => {
    const result = session(USER, "refreshed");
    updateSession.mockResolvedValue(result);
    const res = await middleware(request("/me"));
    expect(res).toBe(result.response);
  });
});

describe("middleware の matcher（config.matcher）", () => {
  const matches = (path: string) => unstable_doesMiddlewareMatch({ config, url: path });

  it("アプリの画面・認証の着地点・画像プロキシでは実行する", () => {
    for (const path of [
      "/",
      "/login",
      "/dm",
      "/dm/abc",
      "/me",
      "/posts/p1",
      "/auth/confirm",
      "/auth/callback",
      "/media/posts/abc",
    ]) {
      expect(matches(path), path).toBe(true);
    }
  });

  it("ビルド成果物・PWA の静的ファイルでは実行しない（毎回のセッション確認を省く）", () => {
    for (const path of [
      "/_next/static/chunks/main.js",
      "/_next/image",
      "/sw.js",
      "/manifest.json",
      "/icons/icon-192.png",
      "/favicon.ico",
      "/robots.txt",
      // 画像の拡張子を持つパスは /media でも middleware を通らない。
      // そのため /media/[...key] の Route Handler 自身がセッションを確認して 401 を返す（app/media/[...key]/route.test.ts）
      "/media/posts/a.jpg",
    ]) {
      expect(matches(path), path).toBe(false);
    }
  });
});
