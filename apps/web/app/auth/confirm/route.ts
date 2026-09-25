import type { EmailOtpType } from "@supabase/supabase-js";
import type { NextRequest } from "next/server";
import { sanitizeNextPath } from "@/lib/auth/redirect";
import { finishSignIn, redirectToLogin } from "@/lib/auth/route-helpers";
import { createSupabaseServerClient } from "@/lib/supabase/server";

export const dynamic = "force-dynamic";

const ALLOWED_TYPES = new Set<EmailOtpType>([
  "email",
  "magiclink",
  "signup",
  "invite",
  "recovery",
  "email_change",
]);

/**
 * マジックリンクの着地点（メールテンプレート infra/supabase/templates/magic_link.html のリンク先）。
 * GET /auth/confirm?token_hash=...&type=email&next=/
 * token_hash を verifyOtp で検証してセッション Cookie を発行し、next へリダイレクトする。
 */
export async function GET(request: NextRequest) {
  const { searchParams } = request.nextUrl;
  const tokenHash = searchParams.get("token_hash");
  const type = searchParams.get("type") as EmailOtpType | null;
  const next = sanitizeNextPath(searchParams.get("next"));

  if (!tokenHash || !type || !ALLOWED_TYPES.has(type)) {
    console.warn("[auth/confirm] missing or invalid token_hash/type");
    return redirectToLogin(request, "link");
  }

  const supabase = await createSupabaseServerClient();
  const { data, error } = await supabase.auth.verifyOtp({ type, token_hash: tokenHash });
  if (error || !data.user) {
    console.warn("[auth/confirm] verifyOtp failed:", error?.code, error?.message);
    return redirectToLogin(request, "link");
  }

  return finishSignIn(request, supabase, data.user.id, next);
}
