import "server-only";
import { createServerClient } from "@supabase/ssr";
import { cookies } from "next/headers";
import type { Database } from "@everkano/shared";
import { getPublicEnv } from "@/lib/env";
import type { TypedSupabaseClient } from "./types";

/**
 * サーバーコンポーネント / Route Handler / Server Action 用の Supabase クライアント。
 * リクエストの Cookie（ユーザーのセッション）で RLS が適用される。
 */
export async function createSupabaseServerClient(): Promise<TypedSupabaseClient> {
  const env = getPublicEnv();
  const cookieStore = await cookies();

  return createServerClient<Database>(env.supabaseUrl, env.supabaseAnonKey, {
    cookies: {
      getAll() {
        return cookieStore.getAll();
      },
      setAll(cookiesToSet) {
        try {
          for (const { name, value, options } of cookiesToSet) {
            cookieStore.set(name, value, options);
          }
        } catch {
          // Server Component から呼ばれた場合 Cookie は書き込めない（Next.js の仕様）。
          // セッションの更新は middleware.ts が毎リクエスト行うため、ここでは無視してよい
          // （Supabase 公式の @supabase/ssr 推奨パターン）。Route Handler では正常に書き込まれる。
        }
      },
    },
  });
}
