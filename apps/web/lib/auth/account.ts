import { useQuery } from "@tanstack/react-query";
import { ApiError } from "@/lib/api/errors";
import { queryKeys } from "@/lib/queries/keys";
import { getSupabaseBrowserClient } from "@/lib/supabase/client";
import { loginPath, type LoginErrorReason } from "./redirect";

/** ログイン中ユーザーの情報（auth.users + 自分の profiles 行） */
export interface MyAccount {
  userId: string;
  email: string | null;
  displayName: string | null;
  /** 退会日時（NULL 以外なら退会済み） */
  deletedAt: string | null;
  /** profiles 行が存在するか（通常は auth トリガーで必ず作られる） */
  profileExists: boolean;
}

/** 自分のアカウント情報を取得する。未ログインなら null */
export async function fetchMyAccount(): Promise<MyAccount | null> {
  const supabase = getSupabaseBrowserClient();
  // getUser() は Auth サーバーに問い合わせてトークンを検証する（削除済みユーザーも検知できる）
  const { data: userData, error: userError } = await supabase.auth.getUser();
  if (userError) {
    if (
      userError.name === "AuthSessionMissingError" ||
      userError.status === 401 ||
      userError.status === 403
    ) {
      return null;
    }
    throw userError;
  }
  const user = userData.user;
  if (!user) return null;

  const { data, error } = await supabase
    .from("profiles")
    .select("id, display_name, deleted_at")
    .eq("id", user.id)
    .maybeSingle();
  if (error) throw error;

  return {
    userId: user.id,
    email: user.email ?? null,
    displayName: data?.display_name ?? null,
    deletedAt: data?.deleted_at ?? null,
    profileExists: Boolean(data),
  };
}

/** 自分のアカウント情報（React Query。キー: queryKeys.profile()） */
export function useMyAccount() {
  return useQuery({
    queryKey: queryKeys.profile(),
    queryFn: fetchMyAccount,
    staleTime: 5 * 60_000,
  });
}

/** 表示名（display_name → メールのローカル部 → 「ゲスト」） */
export function accountDisplayName(account: MyAccount | null | undefined): string {
  if (!account) return "";
  return account.displayName?.trim() || account.email?.split("@")[0] || "ゲスト";
}

let redirecting = false;

/** signOutAndRedirect() によるログイン画面への遷移が進行中か（二重リダイレクト防止） */
export function isRedirectingToLogin(): boolean {
  return redirecting;
}

/**
 * サインアウトしてログイン画面へ（ハードナビゲーションでクライアント状態を完全に破棄する）。
 * scope: "global" は全端末のセッションを無効化（退会時）、"local" はこの端末のみ（ログアウト時）。
 * サインアウト API の失敗は呼び出し元へ返す（握りつぶさない）。
 */
export async function signOutAndRedirect(
  options: { reason?: LoginErrorReason; scope?: "global" | "local"; next?: string } = {},
): Promise<{ error: Error | null }> {
  if (redirecting) return { error: null };
  redirecting = true;
  const supabase = getSupabaseBrowserClient();
  const { error } = await supabase.auth.signOut({ scope: options.scope ?? "local" });
  if (error) {
    redirecting = false;
    console.error("[auth] sign out failed:", error.message);
    return { error };
  }
  window.location.replace(loginPath({ error: options.reason, next: options.next }));
  return { error: null };
}

/**
 * React Query のグローバル onError（lib/query-client.ts）から呼ばれる。
 * - 403 account_deleted → サインアウトして /login?error=withdrawn
 * - 401 unauthorized    → サインアウトして /login?error=session
 */
export function handleGlobalAuthError(error: unknown): void {
  if (!(error instanceof ApiError) || redirecting) return;
  if (error.code === "account_deleted") {
    void signOutAndRedirect({ reason: "withdrawn" });
  } else if (error.code === "unauthorized") {
    void signOutAndRedirect({ reason: "session", next: window.location.pathname });
  }
}
