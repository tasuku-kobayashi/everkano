import { createBrowserClient } from "@supabase/ssr";
import type { Database } from "@everkano/shared";
import { getPublicEnv } from "@/lib/env";
import { createFetchWithTimeout } from "./fetch-timeout";
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
 *
 * 通信の失敗の扱い:
 * - すべてのリクエスト（REST / Auth）に 15 秒のタイムアウトを付ける（通信が固まってもスケルトンのままにしない）。
 * - postgrest-js 自身の自動リトライ（GET を最大 3 回・1/2/4 秒待ち）は無効化する。再試行は React Query の
 *   1 回だけ（lib/query-retry.ts）に一本化し、圏外・DNS 失敗時に約 16 秒もエラー表示が出ない状態を防ぐ。
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
    browserClient = createBrowserClient<Database>(env.supabaseUrl, env.supabaseAnonKey, {
      db: { retry: false },
      global: { fetch: createFetchWithTimeout() },
    });
  }
  return browserClient;
}
