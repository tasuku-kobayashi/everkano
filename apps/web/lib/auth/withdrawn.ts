import type { TypedSupabaseClient } from "@/lib/supabase/types";

/**
 * 退会済み（profiles.deleted_at が非 NULL）かどうか。サーバー / ブラウザ共通。
 * 読み取りに失敗した場合はログを出して false（通す）を返す。
 * 退会済みユーザーの書き込みは API 側（403 account_deleted）でも拒否されるため、ここは UX 目的の判定。
 */
export async function isProfileWithdrawn(
  supabase: TypedSupabaseClient,
  userId: string,
): Promise<boolean> {
  const { data, error } = await supabase
    .from("profiles")
    .select("deleted_at")
    .eq("id", userId)
    .maybeSingle();
  if (error) {
    console.error("[auth] failed to read profile:", error.message);
    return false;
  }
  return Boolean(data?.deleted_at);
}
