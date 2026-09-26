import "server-only";
import { NextResponse, type NextRequest } from "next/server";
import type { TypedSupabaseClient } from "@/lib/supabase/types";
import { loginPath, type LoginErrorReason } from "./redirect";
import { isProfileWithdrawn } from "./withdrawn";

/**
 * 確認画面（components/auth/confirm-login-form.tsx）が fetch で送信したか（Accept: application/json）。
 * その場合は 303 の代わりに遷移先を JSON で返し、画面が location.replace() で遷移する。フォームの送信（ナビゲーション）
 * + 303 では確認画面（/auth/confirm?token_hash=…）が履歴に残り、ログイン後の「戻る」で確認画面へ戻ってしまうため。
 */
export function wantsJsonRedirect(request: NextRequest): boolean {
  return (
    request.method === "POST" && /\bapplication\/json\b/i.test(request.headers.get("accept") ?? "")
  );
}

/** fetch で送信した確認画面への応答の形 */
export interface JsonRedirectBody {
  /** 遷移先（同一オリジンの相対パス） */
  location: string;
}

/**
 * 同一オリジン内のパスへリダイレクト（303: POST 後でも GET で遷移させる）。
 * fetch で送信された場合（wantsJsonRedirect）は 200 + { location } を返す（Cookie の発行は同じ）。
 */
export function redirectTo(request: NextRequest, path: string): NextResponse {
  const url = new URL(path, request.nextUrl.origin);
  if (wantsJsonRedirect(request)) {
    const body: JsonRedirectBody = { location: `${url.pathname}${url.search}${url.hash}` };
    return NextResponse.json(body, { headers: { "Cache-Control": "no-store" } });
  }
  return NextResponse.redirect(url, { status: 303 });
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
