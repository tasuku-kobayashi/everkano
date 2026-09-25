import { NextRequest } from "next/server";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { createFakeSupabaseServer } from "@/test/fake-supabase-server";

/**
 * GET /auth/callback（PKCE: signInWithOtp の emailRedirectTo。Supabase 既定のメールテンプレートを使う環境の着地点）
 */

const fake = createFakeSupabaseServer();
vi.mock("@/lib/supabase/server", () => ({ createSupabaseServerClient: async () => fake.client }));

const { GET } = await import("./route");

const ORIGIN = "https://app.example";

async function callback(query: string) {
  const res = await GET(new NextRequest(`${ORIGIN}/auth/callback${query}`));
  return { status: res.status, location: res.headers.get("location") };
}

describe("GET /auth/callback", () => {
  beforeEach(() => {
    vi.spyOn(console, "warn").mockImplementation(() => undefined);
    vi.spyOn(console, "error").mockImplementation(() => undefined);
    fake.auth.exchangeCodeForSession.mockReset();
    fake.auth.signOut.mockClear();
    fake.profile.deletedAt = null;
    fake.profile.error = null;
    fake.auth.exchangeCodeForSession.mockResolvedValue({
      data: { user: { id: "user-1" }, session: {} },
      error: null,
    });
  });

  it("code をセッションに交換し、next（検証済み）へ 303", async () => {
    await expect(callback("?code=abc&next=%2Fdm%2Fx")).resolves.toEqual({
      status: 303,
      location: `${ORIGIN}/dm/x`,
    });
    expect(fake.auth.exchangeCodeForSession).toHaveBeenCalledWith("abc");
  });

  it("next が外部・認証系のパスなら / へ（オープンリダイレクト防止）", async () => {
    for (const next of ["https://evil.example", "//evil.example", "/\\evil", "/login", "/auth/x"]) {
      const res = await callback(`?code=abc&next=${encodeURIComponent(next)}`);
      expect(res.location, next).toBe(`${ORIGIN}/`);
    }
  });

  it("code が無い・Auth のエラー付きで戻ってきた → /login?error=link（交換しない）", async () => {
    await expect(callback("")).resolves.toEqual({
      status: 303,
      location: `${ORIGIN}/login?error=link`,
    });
    await expect(
      callback("?error=access_denied&error_description=Email+link+is+invalid+or+has+expired"),
    ).resolves.toMatchObject({ location: `${ORIGIN}/login?error=link` });
    expect(fake.auth.exchangeCodeForSession).not.toHaveBeenCalled();
  });

  it("交換に失敗（使用済み・期限切れ・別の端末で開いた）→ /login?error=link", async () => {
    fake.auth.exchangeCodeForSession.mockResolvedValue({
      data: { user: null, session: null },
      error: { code: "flow_state_not_found", message: "invalid flow state" },
    });
    await expect(callback("?code=used")).resolves.toEqual({
      status: 303,
      location: `${ORIGIN}/login?error=link`,
    });
  });

  it("失敗しても遷移先（next）は /login に引き継ぐ（ログインし直した後で元のページへ）", async () => {
    await expect(callback("?next=%2Fposts%2Fp1")).resolves.toMatchObject({
      location: `${ORIGIN}/login?error=link&next=%2Fposts%2Fp1`,
    });
    fake.auth.exchangeCodeForSession.mockResolvedValue({
      data: { user: null, session: null },
      error: { code: "flow_state_not_found", message: "invalid flow state" },
    });
    await expect(callback("?code=used&next=%2Fdm%2Fc1")).resolves.toMatchObject({
      location: `${ORIGIN}/login?error=link&next=%2Fdm%2Fc1`,
    });
    await expect(callback("?code=used&next=%2F%2Fevil.example")).resolves.toMatchObject({
      location: `${ORIGIN}/login?error=link`,
    });
  });

  it("退会済みのアカウントはサインアウトして /login?error=withdrawn", async () => {
    fake.profile.deletedAt = "2026-09-01T00:00:00Z";
    await expect(callback("?code=abc&next=%2Fdm")).resolves.toEqual({
      status: 303,
      location: `${ORIGIN}/login?error=withdrawn`,
    });
    expect(fake.auth.signOut).toHaveBeenCalledWith({ scope: "local" });
  });
});
