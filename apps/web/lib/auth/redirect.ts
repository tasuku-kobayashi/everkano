/**
 * ログイン後の遷移先（?next=）の検証。オープンリダイレクトを防ぐため、
 * 同一オリジンの相対パス（"/" で始まり "//" や "/\" で始まらない）のみ許可する。
 */
export function sanitizeNextPath(next: string | null | undefined, fallback = "/"): string {
  if (!next) return fallback;
  if (!next.startsWith("/") || next.startsWith("//") || next.startsWith("/\\")) return fallback;
  if (/[\u0000-\u001f]/.test(next)) return fallback;
  // 認証系のページへ戻すとループするため除外
  if (next.startsWith("/login") || next.startsWith("/auth/")) return fallback;
  return next;
}

/** ログイン画面の ?error= の値 */
export type LoginErrorReason = "withdrawn" | "link" | "session";

export const LOGIN_ERROR_MESSAGES: Record<LoginErrorReason, string> = {
  withdrawn: "このアカウントは退会済みです",
  link: "ログインリンクが無効か、有効期限が切れています。もう一度ログインリンクを送信するか、メールに記載された確認コードを入力してください。",
  session: "ログインの有効期限が切れました。もう一度ログインしてください。",
};

export function parseLoginError(value: string | null | undefined): LoginErrorReason | null {
  return value === "withdrawn" || value === "link" || value === "session" ? value : null;
}

/** ログイン画面の URL を作る */
export function loginPath(options: { error?: LoginErrorReason; next?: string } = {}): string {
  const params = new URLSearchParams();
  if (options.error) params.set("error", options.error);
  if (options.next && options.next !== "/") params.set("next", options.next);
  const query = params.toString();
  return query ? `/login?${query}` : "/login";
}
