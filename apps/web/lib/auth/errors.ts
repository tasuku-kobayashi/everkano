/**
 * Supabase Auth のエラーを日本語メッセージに変換する。
 * （AuthError の code は https://supabase.com/docs/guides/auth/debugging/error-codes ）
 */

export interface AuthErrorLike {
  message?: string;
  code?: string | undefined;
  status?: number | undefined;
  name?: string;
}

export function authErrorMessage(
  error: AuthErrorLike,
  context: "send" | "verify" = "send",
): string {
  const code = error.code ?? "";
  const status = error.status ?? 0;

  if (
    error.name === "AuthRetryableFetchError" ||
    (status === 0 && /fetch|network/i.test(error.message ?? ""))
  ) {
    return "通信できませんでした。電波の良い場所で再度お試しください";
  }
  if (
    code === "over_email_send_rate_limit" ||
    code === "over_request_rate_limit" ||
    status === 429
  ) {
    return "送信回数の上限に達しました。しばらく時間をおいてから再度お試しください";
  }
  if (code === "email_address_invalid" || code === "validation_failed") {
    return "メールアドレスの形式が正しくありません";
  }
  if (code === "signup_disabled" || code === "email_provider_disabled") {
    return "現在、新規登録を受け付けていません";
  }
  if (
    code === "otp_expired" ||
    code === "otp_disabled" ||
    (context === "verify" && status === 403)
  ) {
    return "確認コードが正しくないか、有効期限が切れています";
  }
  if (code === "user_banned") {
    return "このアカウントは利用できません";
  }
  return context === "verify"
    ? "ログインできませんでした。もう一度お試しください"
    : "ログインリンクを送信できませんでした。しばらくしてから再度お試しください";
}

/** 簡易メールアドレス検証（最終的な判定は Supabase Auth が行う） */
export function isValidEmail(value: string): boolean {
  return /^[^\s@]+@[^\s@]+\.[^\s@]+$/.test(value.trim());
}
