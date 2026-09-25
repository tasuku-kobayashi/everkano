import type { SupabaseClient } from "@supabase/supabase-js";
import type { Database } from "@everkano/shared";

/** Database 型付きの Supabase クライアント（ブラウザ / サーバー共通） */
export type TypedSupabaseClient = SupabaseClient<Database>;
