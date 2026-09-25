import { NextResponse, type NextRequest } from "next/server";
import { redirectWithSession, updateSession } from "@/lib/supabase/middleware";

/**
 * 認証ミドルウェア
 * - 全リクエストで Supabase セッションをリフレッシュ（Cookie 更新）
 * - 未ログインで保護ページにアクセス → /login?next=<元のパス> へリダイレクト
 * - ログイン済みで /login にアクセス → / へリダイレクト
 * - 退会済み（profiles.deleted_at）判定は DB 参照が必要なため、ここではなく
 *   /auth/confirm・/auth/callback（ログイン時）と (main) レイアウトの AccountGuard で行う
 */

const PUBLIC_PATHS = new Set(["/login", "/offline", "/manifest.json", "/sw.js", "/favicon.ico"]);
const PUBLIC_PREFIXES = ["/auth/", "/icons/"];

function isPublicPath(pathname: string): boolean {
  return PUBLIC_PATHS.has(pathname) || PUBLIC_PREFIXES.some((p) => pathname.startsWith(p));
}

export async function middleware(request: NextRequest) {
  const { response, userId } = await updateSession(request);
  const { pathname, search } = request.nextUrl;

  if (!userId && !isPublicPath(pathname)) {
    // 画像プロキシ（/media）は <img> から読まれるため、リダイレクトではなく 401 を返す
    if (pathname.startsWith("/media/")) {
      return new NextResponse(null, { status: 401 });
    }
    const url = request.nextUrl.clone();
    url.pathname = "/login";
    url.search = "";
    if (pathname !== "/") {
      url.searchParams.set("next", `${pathname}${search}`);
    }
    return redirectWithSession(response, url);
  }

  // ログイン済みユーザーがログイン画面を開いた場合はホームへ（退会エラー表示中は除く）
  if (
    userId &&
    pathname === "/login" &&
    request.nextUrl.searchParams.get("error") !== "withdrawn"
  ) {
    const url = request.nextUrl.clone();
    url.pathname = "/";
    url.search = "";
    return redirectWithSession(response, url);
  }

  return response;
}

export const config = {
  matcher: [
    /*
     * 以下を除くすべてのパスで実行:
     * - _next/static, _next/image（ビルド成果物）
     * - icons/, sw.js, manifest.json, favicon.ico, robots.txt（PWA / 静的ファイル）
     * - 画像などの拡張子付き静的ファイル
     */
    "/((?!_next/static|_next/image|icons/|sw\\.js|manifest\\.json|favicon\\.ico|robots\\.txt|.*\\.(?:png|jpg|jpeg|gif|webp|avif|svg|ico|txt|xml|webmanifest)$).*)",
  ],
};
