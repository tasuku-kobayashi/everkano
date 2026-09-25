import "server-only";
import { NextResponse, type NextRequest } from "next/server";
import type { TypedSupabaseClient } from "@/lib/supabase/types";
import { loginPath, type LoginErrorReason } from "./redirect";
import { isProfileWithdrawn } from "./withdrawn";

/** 同一オリジン内のパスへリダイレクト（303: POST 後でも GET で遷移させる） */
export function redirectTo(request: NextRequest, path: string): NextResponse {
  return NextResponse.redirect(new URL(path, request.nextUrl.origin), { status: 303 });
}

/**
 * ログイン画面へ（エラー表示付き）。next（検証済みの相対パス）を渡すと、ログインし直した後にそのページへ戻す
 * （期限切れのリンクを開いた場合など）。
 */
export function redirectToLogin(
  request: NextRequest,
  error: LoginErrorReason,
  next?: string,
): NextResponse {
  return redirectTo(request, loginPath({ error, next }));
}

/**
 * ログイン成功直後の共通処理: 退会済みならサインアウトして /login?error=withdrawn、そうでなければ next へ。
 */
export async function finishSignIn(
  request: NextRequest,
  supabase: TypedSupabaseClient,
  userId: string,
  next: string,
): Promise<NextResponse> {
  if (await isProfileWithdrawn(supabase, userId)) {
    const { error } = await supabase.auth.signOut({ scope: "local" });
    if (error) console.error("[auth] sign out (withdrawn) failed:", error.message);
    return redirectToLogin(request, "withdrawn");
  }
  return redirectTo(request, next);
}
