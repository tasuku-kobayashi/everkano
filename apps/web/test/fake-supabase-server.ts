import { vi } from "vitest";

/**
 * Route Handler のテスト用: createSupabaseServerClient() が返すクライアントの最小限の偽物。
 * auth（コード交換・OTP 検証・サインアウト）と profiles.deleted_at の読み取りだけを持つ。
 */
export function createFakeSupabaseServer() {
  const profile = { deletedAt: null as string | null, error: null as { message: string } | null };
  const auth = {
    exchangeCodeForSession: vi.fn(),
    verifyOtp: vi.fn(),
    signOut: vi.fn(async () => ({ error: null })),
    getClaims: vi.fn(async () => ({ data: null, error: null })),
  };
  const client = {
    auth,
    from: vi.fn((_table: string) => ({
      select: () => ({
        eq: () => ({
          maybeSingle: async () =>
            profile.error
              ? { data: null, error: profile.error }
              : { data: { deleted_at: profile.deletedAt }, error: null },
        }),
      }),
    })),
  };
  return { client, auth, profile };
}
