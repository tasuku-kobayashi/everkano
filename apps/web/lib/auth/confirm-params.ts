import type { EmailOtpType } from "@supabase/supabase-js";
import { nextPathFromRedirectTo, sanitizeNextPath } from "./redirect";

/** マジックリンクで受け付ける type（infra/supabase/templates/*.html のリンクと一致させる） */
const ALLOWED_TYPES: ReadonlySet<string> = new Set<EmailOtpType>([
  "email",
  "magiclink",
  "signup",
  "invite",
  "recovery",
  "email_change",
]);

export interface ConfirmParams {
  tokenHash: string;
  type: EmailOtpType;
  /** 検証済みの相対パス */
  next: string;
}

/** /auth/confirm のクエリ・/auth/confirm/verify のフォームの項目名 */
export type ConfirmParamName = "token_hash" | "type" | "next" | "redirect_to";

/**
 * マジックリンク（/auth/confirm）のログイン後の遷移先（検証済みの相対パス）。次の順に決める
 * （どれも sanitizeNextPath で同一オリジンの相対パスに限る）:
 * 1. redirect_to（メールのリンク。templates/magic_link.html が emailRedirectTo = `<SITE_URL>/auth/callback?next=..`
 *    をそのまま運ぶ）の中の next
 * 2. next（確認画面のフォームの POST、テンプレートが next=/ を直接付けていた頃のメール）
 * 3. どちらも無ければ "/"
 * トークンが不正でも使う（/login?error=link&next= に引き継ぎ、ログインし直した後で元のページへ戻す）。
 */
export function parseConfirmNext(get: (name: ConfirmParamName) => unknown): string {
  const redirectTo = get("redirect_to");
  const fromRedirect = typeof redirectTo === "string" ? nextPathFromRedirectTo(redirectTo) : null;
  if (fromRedirect) return fromRedirect;
  const next = get("next");
  return sanitizeNextPath(typeof next === "string" ? next : null);
}

/**
 * マジックリンク（/auth/confirm）のパラメーターを検証する。不正なら null。
 * GET（確認画面）と POST（/auth/confirm/verify）で共通。
 */
export function parseConfirmParams(get: (name: ConfirmParamName) => unknown): ConfirmParams | null {
  const tokenHash = get("token_hash");
  const type = get("type");
  if (typeof tokenHash !== "string" || !/^[A-Za-z0-9_-]{8,256}$/.test(tokenHash)) return null;
  if (typeof type !== "string" || !ALLOWED_TYPES.has(type)) return null;
  return { tokenHash, type: type as EmailOtpType, next: parseConfirmNext(get) };
}
