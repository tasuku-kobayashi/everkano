import type { NextRequest } from "next/server";
import { sanitizeNextPath } from "@/lib/auth/redirect";
import { finishSignIn, redirectToLogin } from "@/lib/auth/route-helpers";
import { createSupabaseServerClient } from "@/lib/supabase/server";

export const dynamic = "force-dynamic";

/**
 * PKCE フローの戻り先（signInWithOtp の emailRedirectTo = lib/auth/redirect.ts の emailRedirectUrl）。
 * GET /auth/callback?next=/posts/..&code=...
 * Supabase 既定のメールテンプレート（{{ .ConfirmationURL }}）を使う環境ではこちらに着地する。
 * code を exchangeCodeForSession でセッションに交換する（コード検証子 Cookie は送信した端末のブラウザにある）。
 */
export async function GET(request: NextRequest) {
  const { searchParams } = request.nextUrl;
  const code = searchParams.get("code");
  const next = sanitizeNextPath(searchParams.get("next"));

  const authError = searchParams.get("error_description") ?? searchParams.get("error");
  if (authError) {
    console.warn("[auth/callback] auth error:", authError);
    return redirectToLogin(request, "link", next);
  }
  if (!code) {
    console.warn("[auth/callback] missing code");
    return redirectToLogin(request, "link", next);
  }

  const supabase = await createSupabaseServerClient();
  const { data, error } = await supabase.auth.exchangeCodeForSession(code);
  if (error || !data.user) {
    console.warn("[auth/callback] exchangeCodeForSession failed:", error?.code, error?.message);
    return redirectToLogin(request, "link", next);
  }

  return finishSignIn(request, supabase, data.user.id, next);
}
