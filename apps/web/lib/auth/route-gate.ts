import { isSessionEndingLoginError, parseLoginError, sanitizeNextPath } from "./redirect";

/**
 * 認証ミドルウェア（middleware.ts）の判定ロジック。Next.js に依存しない純粋関数にしてテストする
 * （lib/auth/route-gate.test.ts / middleware.test.ts）。
 *
 * - 未ログインで保護ページ → /login?next=<元のパス>（/ は next なし）
 * - 未ログインで /media/*（<img> から読まれる署名 URL の発行）→ リダイレクトではなく 401
 * - ログイン済みで /login → ?next=（検証済みの相対パス。無ければ /）。ただし ?error=withdrawn / session / banned は除く
 *   （別のタブでログインした後にログイン画面を開き直した場合も、開こうとしていたページへ進める）
 *   （Cookie の JWT はローカル検証で有効に見えても、ユーザー削除・利用停止などで実際には使えないセッション。
 *   / へ戻すと AccountGuard が再びログイン画面へ送り、リダイレクトが無限に繰り返される）
 */

/** ログインなしで開けるパス（完全一致） */
export const PUBLIC_PATHS: ReadonlySet<string> = new Set([
  "/login",
  "/offline",
  "/manifest.json",
  "/sw.js",
  "/favicon.ico",
]);

/** ログインなしで開けるパス（前方一致）。/auth/*: マジックリンクの着地点・PKCE の戻り先 */
export const PUBLIC_PREFIXES: readonly string[] = ["/auth/", "/icons/"];

export function isPublicPath(pathname: string): boolean {
  return PUBLIC_PATHS.has(pathname) || PUBLIC_PREFIXES.some((p) => pathname.startsWith(p));
}

export type AuthGateDecision =
  | { action: "next" }
  /** 本文なしの 401（画像プロキシ /media） */
  | { action: "unauthorized" }
  /** 同一オリジン内へのリダイレクト（セッション Cookie を引き継ぐこと） */
  | { action: "redirect"; pathname: string; search: string };

export interface AuthGateInput {
  pathname: string;
  /** "?a=b" 形式（無ければ ""） */
  search: string;
  /** 検証済みのユーザー ID（未ログイン・トークン不正なら null） */
  userId: string | null;
  /** /login?error= の値 */
  loginError: string | null;
}

export function decideAuthGate({
  pathname,
  search,
  userId,
  loginError,
}: AuthGateInput): AuthGateDecision {
  if (!userId && !isPublicPath(pathname)) {
    if (pathname.startsWith("/media/")) return { action: "unauthorized" };
    const next =
      pathname === "/" ? "" : `?${new URLSearchParams({ next: `${pathname}${search}` })}`;
    return { action: "redirect", pathname: "/login", search: next };
  }

  // ログイン済みユーザーがログイン画面を開いた場合は ?next=（無ければホーム）へ（セッション終了のエラー表示中は除く）
  if (userId && pathname === "/login" && !isSessionEndingLoginError(parseLoginError(loginError))) {
    const next = new URL(sanitizeNextPath(new URLSearchParams(search).get("next")), "http://x");
    return { action: "redirect", pathname: next.pathname, search: next.search };
  }

  return { action: "next" };
}
