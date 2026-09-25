import { NextRequest } from "next/server";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { createFakeSupabaseServer } from "@/test/fake-supabase-server";

/**
 * POST /auth/confirm/verify（マジックリンクの確認画面の「ログインする」）。
 * 同一オリジンからのフォーム送信だけを受け付ける（ログイン CSRF 対策）。
 */

const fake = createFakeSupabaseServer();
vi.mock("@/lib/supabase/server", () => ({ createSupabaseServerClient: async () => fake.client }));

const { POST } = await import("./route");

const ORIGIN = "https://app.example";
const TOKEN = "pkce_0123456789abcdef";

function form(fields: Record<string, string>) {
  return new URLSearchParams(fields).toString();
}

async function verify(
  body: string,
  headers: Record<string, string> = { "sec-fetch-site": "same-origin" },
) {
  const res = await POST(
    new NextRequest(`${ORIGIN}/auth/confirm/verify`, {
      method: "POST",
      body,
      headers: { "content-type": "application/x-www-form-urlencoded", ...headers },
    }),
  );
  return { status: res.status, location: res.headers.get("location") };
}

describe("POST /auth/confirm/verify", () => {
  beforeEach(() => {
    vi.spyOn(console, "warn").mockImplementation(() => undefined);
    vi.spyOn(console, "error").mockImplementation(() => undefined);
    fake.auth.verifyOtp.mockReset();
    fake.auth.signOut.mockClear();
    fake.profile.deletedAt = null;
    fake.auth.verifyOtp.mockResolvedValue({ data: { user: { id: "user-1" } }, error: null });
  });

  it("同一オリジンからの送信でトークンを検証し、next へ 303", async () => {
    await expect(verify(form({ token_hash: TOKEN, type: "email", next: "/me" }))).resolves.toEqual({
      status: 303,
      location: `${ORIGIN}/me`,
    });
    expect(fake.auth.verifyOtp).toHaveBeenCalledWith({ type: "email", token_hash: TOKEN });
  });

  it("Sec-Fetch-Site が無い古いブラウザは Origin で判定する", async () => {
    await expect(
      verify(form({ token_hash: TOKEN, type: "email" }), { origin: ORIGIN }),
    ).resolves.toMatchObject({ location: `${ORIGIN}/` });
  });

  it("他サイトからの送信（ログイン CSRF）・送信元が不明な要求はトークンを消費せずに拒否", async () => {
    const cases: Record<string, string>[] = [
      { "sec-fetch-site": "cross-site", origin: "https://evil.example" },
      { "sec-fetch-site": "same-site", origin: "https://sub.app.example" },
      { origin: "https://evil.example" },
      {},
    ];
    for (const headers of cases) {
      await expect(
        verify(form({ token_hash: TOKEN, type: "email" }), headers),
        JSON.stringify(headers),
      ).resolves.toEqual({ status: 303, location: `${ORIGIN}/login?error=link` });
    }
    expect(fake.auth.verifyOtp).not.toHaveBeenCalled();
  });

  it("token_hash・type が不正なら検証しない", async () => {
    for (const body of [
      form({ type: "email" }),
      form({ token_hash: "short", type: "email" }),
      form({ token_hash: TOKEN, type: "sms" }),
      "not a form",
    ]) {
      await expect(verify(body), body).resolves.toMatchObject({
        location: `${ORIGIN}/login?error=link`,
      });
    }
    expect(fake.auth.verifyOtp).not.toHaveBeenCalled();
  });

  it("検証に失敗（使用済み・期限切れ）→ /login?error=link", async () => {
    fake.auth.verifyOtp.mockResolvedValue({
      data: { user: null },
      error: { code: "otp_expired", message: "expired" },
    });
    await expect(verify(form({ token_hash: TOKEN, type: "email" }))).resolves.toMatchObject({
      location: `${ORIGIN}/login?error=link`,
    });
  });

  it("期限切れ・使用済みのリンクでも、ログインし直した後の遷移先（next）を /login に引き継ぐ", async () => {
    fake.auth.verifyOtp.mockResolvedValue({
      data: { user: null },
      error: { code: "otp_expired", message: "expired" },
    });
    await expect(
      verify(form({ token_hash: TOKEN, type: "email", next: "/posts/p1" })),
    ).resolves.toMatchObject({ location: `${ORIGIN}/login?error=link&next=%2Fposts%2Fp1` });
    // token_hash が不正な場合も同じ。外部の next は引き継がない
    await expect(verify(form({ type: "email", next: "/dm/c1" }))).resolves.toMatchObject({
      location: `${ORIGIN}/login?error=link&next=%2Fdm%2Fc1`,
    });
    await expect(
      verify(form({ token_hash: TOKEN, type: "email", next: "//evil.example" })),
    ).resolves.toMatchObject({ location: `${ORIGIN}/login?error=link` });
  });

  it("退会済みならサインアウトして /login?error=withdrawn", async () => {
    fake.profile.deletedAt = "2026-09-01T00:00:00Z";
    await expect(verify(form({ token_hash: TOKEN, type: "email" }))).resolves.toMatchObject({
      location: `${ORIGIN}/login?error=withdrawn`,
    });
    expect(fake.auth.signOut).toHaveBeenCalledWith({ scope: "local" });
  });
});
