import { API_ERROR_MESSAGES, SESSION_EXPIRED_MESSAGE } from "@/lib/api/errors";

/** 運営によって利用停止（Supabase Auth の ban）されたアカウントの文言 */
export const ACCOUNT_BANNED_MESSAGE = "このアカウントは利用停止中です。";

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

/** マジックリンクの戻り先のパス（signInWithOtp の emailRedirectTo。app/auth/callback/route.ts） */
export const EMAIL_REDIRECT_PATH = "/auth/callback";

/**
 * signInWithOtp の emailRedirectTo（`<SITE_URL>/auth/callback?next=<ログイン後の遷移先>`）。
 * ログイン前に開こうとしていたページ（/login?next=）をメールまで運ぶ。GoTrue はこの値を許可リスト
 * （infra/supabase/config.toml の additional_redirect_urls / Site URL）で検証し、メールのリンクの redirect_to に入れる
 * （templates/magic_link.html）。/auth/confirm は nextPathFromRedirectTo で next を取り出す。
 * next が "/"（または不正）なら next を付けない。
 */
export function emailRedirectUrl(siteUrl: string, nextPath: string): string {
  const base = `${siteUrl.replace(/\/+$/, "")}${EMAIL_REDIRECT_PATH}`;
  const next = sanitizeNextPath(nextPath);
  return next === "/" ? base : `${base}?${new URLSearchParams({ next }).toString()}`;
}

/**
 * メールのリンク（/auth/confirm?token_hash=..&type=email&redirect_to=<emailRedirectTo>）の redirect_to から、
 * ログイン後の遷移先（next）を取り出す。取り出せなければ null。
 *
 * redirect_to は GoTrue が許可リストで検証した値だが、リンクの URL は誰でも作れるので、ここでは信用しない:
 * - http(s) の URL で、パスが /auth/callback（emailRedirectTo の形）のときだけ ?next= を読む
 * - next は sanitizeNextPath で同一オリジンの相対パスか検証する（redirect_to のオリジンは使わない = オープンリダイレクトにならない）
 * 許可されなかった emailRedirectTo は GoTrue が Site URL に置き換える（パスが違うので null → ホームへ）。
 */
export function nextPathFromRedirectTo(redirectTo: string | null | undefined): string | null {
  if (!redirectTo || redirectTo.length > 2048) return null;
  let url: URL;
  try {
    url = new URL(redirectTo);
  } catch {
    return null;
  }
  if (url.protocol !== "https:" && url.protocol !== "http:") return null;
  if (url.pathname.replace(/\/+$/, "") !== EMAIL_REDIRECT_PATH) return null;
  const next = sanitizeNextPath(url.searchParams.get("next"), "");
  return next === "" ? null : next;
}

/** ログイン画面の ?error= の値 */
export type LoginErrorReason = "withdrawn" | "link" | "session" | "banned";

/** ログイン画面のエラー表示（文言の決まりは lib/api/errors.ts の API_ERROR_MESSAGES と同じ） */
export const LOGIN_ERROR_MESSAGES: Record<LoginErrorReason, string> = {
  withdrawn: API_ERROR_MESSAGES.account_deleted,
  link: "ログインリンクが無効か、有効期限が切れています。もう一度ログインリンクを送信するか、メールに記載された確認コードを入力してください。",
  session: SESSION_EXPIRED_MESSAGE,
  banned: ACCOUNT_BANNED_MESSAGE,
};

const LOGIN_ERROR_REASONS = new Set<string>(Object.keys(LOGIN_ERROR_MESSAGES));

export function parseLoginError(value: string | null | undefined): LoginErrorReason | null {
  return value && LOGIN_ERROR_REASONS.has(value) ? (value as LoginErrorReason) : null;
}

/**
 * 「この端末のセッションはもう使えない」ことを表すエラー。ログイン画面はこの端末のセッションを破棄し、
 * middleware は（Cookie の JWT がまだ期限内でも）ログイン画面を表示する（/ へ戻さない = リダイレクトループ防止）。
 * link は含めない（ログイン済みの人が使用済みのリンクを開いた場合は、そのままホームへ戻せばよい）。
 */
export function isSessionEndingLoginError(reason: LoginErrorReason | null): boolean {
  return reason === "withdrawn" || reason === "session" || reason === "banned";
}

/** ログイン画面の URL を作る */
export function loginPath(options: { error?: LoginErrorReason; next?: string } = {}): string {
  const params = new URLSearchParams();
  if (options.error) params.set("error", options.error);
  if (options.next && options.next !== "/") params.set("next", options.next);
  const query = params.toString();
  return query ? `/login?${query}` : "/login";
}
