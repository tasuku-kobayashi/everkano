import { NextResponse, type NextRequest } from "next/server";
import { decideAuthGate } from "@/lib/auth/route-gate";
import { redirectWithSession, updateSession } from "@/lib/supabase/middleware";

/**
 * 認証ミドルウェア
 * - 全リクエストで Supabase セッションをリフレッシュ（Cookie 更新）
 * - 未ログインで保護ページにアクセス → /login?next=<元のパス> へリダイレクト（/media/* は 401）
 * - ログイン済みで /login にアクセス → / へリダイレクト。ただし ?error=withdrawn / session / banned は除く
 * - 判定（公開パスの一覧など）は lib/auth/route-gate.ts。テストは middleware.test.ts / lib/auth/route-gate.test.ts
 * - 退会済み（profiles.deleted_at）判定は DB 参照が必要なため、ここではなく
 *   /auth/confirm・/auth/callback（ログイン時）と (main) レイアウトの AccountGuard で行う
 */
export async function middleware(request: NextRequest) {
  const { response, userId } = await updateSession(request);
  const { pathname, search } = request.nextUrl;

  const decision = decideAuthGate({
    pathname,
    search,
    userId,
    loginError: request.nextUrl.searchParams.get("error"),
  });

  if (decision.action === "unauthorized") {
    // 画像プロキシ（/media）は <img> から読まれるため、リダイレクトではなく 401 を返す
    return new NextResponse(null, { status: 401 });
  }
  if (decision.action === "redirect") {
    const url = request.nextUrl.clone();
    url.pathname = decision.pathname;
    url.search = decision.search;
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
     * 変更したら middleware.test.ts の「matcher」のテストも更新すること。
     */
    "/((?!_next/static|_next/image|icons/|sw\\.js|manifest\\.json|favicon\\.ico|robots\\.txt|.*\\.(?:png|jpg|jpeg|gif|webp|avif|svg|ico|txt|xml|webmanifest)$).*)",
  ],
};
