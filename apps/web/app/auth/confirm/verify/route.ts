import type { NextRequest } from "next/server";
import { parseConfirmNext, parseConfirmParams } from "@/lib/auth/confirm-params";
import { isSameOriginRequest } from "@/lib/auth/csrf";
import { finishSignIn, redirectToLogin } from "@/lib/auth/route-helpers";
import { createSupabaseServerClient } from "@/lib/supabase/server";

export const dynamic = "force-dynamic";

/**
 * マジックリンクのログイン確定（app/auth/confirm/page.tsx の「ログインする」ボタンのフォーム送信先）。
 * POST /auth/confirm/verify  (application/x-www-form-urlencoded: token_hash, type, next)
 * next は確認画面がメールのリンクの redirect_to から取り出した、ログイン後の遷移先（lib/auth/confirm-params.ts）。
 *
 * GET（リンクを開いただけ）ではログインしない:
 * - メールのセキュリティスキャナー（Outlook Safe Links 等）がリンクを先読みしてもトークンが消費されない
 * - 他人が自分宛てに発行したリンクを踏まされても、黙ってその人のアカウントに切り替わらない（ログイン CSRF）
 * 同じオリジンのページからのフォーム送信だけを受け付ける（Sec-Fetch-Site / Origin）。
 */
export async function POST(request: NextRequest) {
  if (!isSameOriginRequest(request.headers, request.nextUrl.origin)) {
    console.warn(
      "[auth/confirm] rejected cross-site POST:",
      request.headers.get("sec-fetch-site") ?? "-",
      request.headers.get("origin") ?? "-",
    );
    return redirectToLogin(request, "link");
  }

  const form = await request.formData().catch(() => null);
  const params = form ? parseConfirmParams((name) => form.get(name)) : null;
  if (!params) {
    console.warn("[auth/confirm] missing or invalid token_hash/type");
    return redirectToLogin(
      request,
      "link",
      form ? parseConfirmNext((name) => form.get(name)) : undefined,
    );
  }

  const supabase = await createSupabaseServerClient();
  const { data, error } = await supabase.auth.verifyOtp({
    type: params.type,
    token_hash: params.tokenHash,
  });
  if (error || !data.user) {
    console.warn("[auth/confirm] verifyOtp failed:", error?.code, error?.message);
    // 期限切れ・使用済みのリンクでも、ログインし直した後は開こうとしていたページへ戻す
    return redirectToLogin(request, "link", params.next);
  }

  return finishSignIn(request, supabase, data.user.id, params.next);
}
