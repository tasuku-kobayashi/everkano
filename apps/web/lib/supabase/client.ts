import { createBrowserClient } from "@supabase/ssr";
import type { Database } from "@everkano/shared";
import { getPublicEnv } from "@/lib/env";
import type { TypedSupabaseClient } from "./types";

/**
 * ブラウザ用 Supabase クライアント（シングルトン）。
 *
 * - セッションは Cookie に保存され、middleware.ts / サーバーコンポーネントと共有される。
 * - 読み取り（フィード・投稿・コメント・DM・メモリ一覧）はこのクライアントで Supabase に直接問い合わせる（RLS 適用）。
 * - ユーザー由来テキストの書き込みは lib/api/client.ts（Python API）経由で行うこと（Gate #1 + 監査ログ）。
 * - characters は `select('*')` 不可。`PUBLIC_CHARACTER_COLUMNS`（@everkano/shared）を使う。
 * - クライアントコンポーネントのイベントハンドラ / useEffect / React Query の queryFn 内でのみ呼ぶこと
 *   （レンダー中やサーバーでは呼ばない。サーバーでは lib/supabase/server.ts を使う）。
 */
let browserClient: TypedSupabaseClient | undefined;

export function getSupabaseBrowserClient(): TypedSupabaseClient {
  if (typeof window === "undefined") {
    throw new Error(
      "getSupabaseBrowserClient() はブラウザ専用です。サーバーでは createSupabaseServerClient() を使ってください。",
    );
  }
  if (!browserClient) {
    const env = getPublicEnv();
    browserClient = createBrowserClient<Database>(env.supabaseUrl, env.supabaseAnonKey);
  }
  return browserClient;
}
