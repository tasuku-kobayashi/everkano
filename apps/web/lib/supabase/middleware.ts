import { createServerClient } from "@supabase/ssr";
import { NextResponse, type NextRequest } from "next/server";
import type { Database } from "@everkano/shared";
import { getPublicEnv } from "@/lib/env";

export interface SessionResult {
  /** Cookie（リフレッシュ後のセッション）を反映済みのレスポンス。リダイレクトする場合も Cookie を引き継ぐこと */
  response: NextResponse;
  /** 検証済みのユーザーID（未ログイン・トークン不正なら null） */
  userId: string | null;
}

/**
 * middleware から呼ぶセッション更新ヘルパー（@supabase/ssr 推奨パターン）。
 * - 期限切れ間近のアクセストークンをリフレッシュし、Cookie をリクエスト/レスポンス双方に書き戻す。
 * - getClaims() は非対称鍵（ES256 等）なら JWKS でローカル検証、HS256 の場合は Auth サーバーに問い合わせる。
 */
export async function updateSession(request: NextRequest): Promise<SessionResult> {
  const env = getPublicEnv();
  let response = NextResponse.next({ request });

  const supabase = createServerClient<Database>(env.supabaseUrl, env.supabaseAnonKey, {
    cookies: {
      getAll() {
        return request.cookies.getAll();
      },
      setAll(cookiesToSet) {
        for (const { name, value } of cookiesToSet) {
          request.cookies.set(name, value);
        }
        response = NextResponse.next({ request });
        for (const { name, value, options } of cookiesToSet) {
          response.cookies.set(name, value, options);
        }
      },
    },
  });

  // 注意: createServerClient と getClaims() の間に処理を挟まないこと（セッションが不整合になり得る）
  const { data, error } = await supabase.auth.getClaims();
  if (error && error.name !== "AuthSessionMissingError") {
    console.warn("[middleware] session verification failed:", error.message);
  }
  const sub = data?.claims?.sub;
  return { response, userId: typeof sub === "string" && sub.length > 0 ? sub : null };
}

/** updateSession の Cookie を引き継いだリダイレクトレスポンスを作る */
export function redirectWithSession(from: NextResponse, url: URL): NextResponse {
  const redirect = NextResponse.redirect(url);
  for (const cookie of from.cookies.getAll()) {
    redirect.cookies.set(cookie);
  }
  return redirect;
}
