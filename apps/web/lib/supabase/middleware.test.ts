import { NextRequest, NextResponse } from "next/server";
import { beforeEach, describe, expect, it, vi } from "vitest";

/**
 * updateSession（middleware のセッション確認・Cookie の更新）のテスト。
 * @supabase/ssr の createServerClient を差し替え、getClaims() の結果と Cookie の書き戻しを確認する。
 */

interface CookieAdapter {
  getAll(): { name: string; value: string }[];
  setAll(cookies: { name: string; value: string; options: Record<string, unknown> }[]): void;
}

const getClaims = vi.fn();
let adapter: CookieAdapter | undefined;
vi.mock("@supabase/ssr", () => ({
  createServerClient: (_url: string, _key: string, options: { cookies: CookieAdapter }) => {
    adapter = options.cookies;
    return { auth: { getClaims } };
  },
}));
vi.mock("@/lib/env", () => ({
  getPublicEnv: () => ({ supabaseUrl: "http://supabase.test", supabaseAnonKey: "anon" }),
}));

const { redirectWithSession, updateSession } = await import("./middleware");

function request(cookie?: string) {
  return new NextRequest("https://app.example/dm", {
    headers: cookie ? { cookie } : {},
  });
}

describe("updateSession", () => {
  beforeEach(() => {
    getClaims.mockReset();
    adapter = undefined;
  });

  it("検証済みの JWT なら sub をユーザー ID として返す", async () => {
    getClaims.mockResolvedValue({ data: { claims: { sub: "user-1" } }, error: null });
    await expect(updateSession(request("sb-x-auth-token=abc"))).resolves.toMatchObject({
      userId: "user-1",
    });
  });

  it("セッションなし・検証失敗（署名不正・期限切れでリフレッシュ不可）は null", async () => {
    getClaims.mockResolvedValue({
      data: null,
      error: { name: "AuthSessionMissingError", message: "Auth session missing!" },
    });
    await expect(updateSession(request())).resolves.toMatchObject({ userId: null });

    const warn = vi.spyOn(console, "warn").mockImplementation(() => undefined);
    getClaims.mockResolvedValue({
      data: null,
      error: { name: "AuthInvalidJwtError", message: "invalid signature" },
    });
    await expect(updateSession(request("sb-x-auth-token=forged"))).resolves.toMatchObject({
      userId: null,
    });
    expect(warn).toHaveBeenCalled();
    warn.mockRestore();

    getClaims.mockResolvedValue({ data: { claims: { sub: "" } }, error: null });
    await expect(updateSession(request())).resolves.toMatchObject({ userId: null });
  });

  it("リフレッシュした Cookie をレスポンスに書き戻し、リダイレクトにも引き継ぐ", async () => {
    getClaims.mockImplementation(async () => {
      adapter?.setAll([
        { name: "sb-x-auth-token", value: "rotated", options: { path: "/", httpOnly: false } },
      ]);
      return { data: { claims: { sub: "user-1" } }, error: null };
    });
    const { response, userId } = await updateSession(request("sb-x-auth-token=old"));
    expect(userId).toBe("user-1");
    expect(response.cookies.get("sb-x-auth-token")?.value).toBe("rotated");

    const redirect = redirectWithSession(response, new URL("https://app.example/login"));
    expect(redirect.status).toBe(307);
    expect(redirect.cookies.get("sb-x-auth-token")?.value).toBe("rotated");
  });

  it("リクエストの Cookie を Supabase クライアントに渡す", async () => {
    getClaims.mockResolvedValue({ data: { claims: { sub: "user-1" } }, error: null });
    await updateSession(request("sb-x-auth-token=abc; other=1"));
    expect(adapter?.getAll()).toEqual(
      expect.arrayContaining([expect.objectContaining({ name: "sb-x-auth-token", value: "abc" })]),
    );
  });
});

describe("redirectWithSession", () => {
  it("Cookie が無ければ通常のリダイレクト", () => {
    const res = redirectWithSession(NextResponse.next(), new URL("https://app.example/"));
    expect(res.headers.get("location")).toBe("https://app.example/");
    expect(res.cookies.getAll()).toEqual([]);
  });
});
