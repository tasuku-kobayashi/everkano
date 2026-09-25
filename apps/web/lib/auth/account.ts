import { useQuery } from "@tanstack/react-query";
import { ApiError, toAppError } from "@/lib/api/errors";
import { queryKeys } from "@/lib/queries/keys";
import { getSupabaseBrowserClient } from "@/lib/supabase/client";
import type { TypedSupabaseClient } from "@/lib/supabase/types";
import { ACCOUNT_BANNED_MESSAGE, loginPath, type LoginErrorReason } from "./redirect";

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

/**
 * 運営によって利用停止（Supabase Auth の ban）されたアカウント。
 * status を持つので React Query は再試行しない（lib/query-retry.ts）。AccountGuard がログイン画面へ送る。
 */
export class AccountBannedError extends Error {
  readonly status = 403;
  readonly code = "user_banned";

  constructor() {
    super(ACCOUNT_BANNED_MESSAGE);
    this.name = "AccountBannedError";
  }
}

interface AuthErrorLike {
  name?: string;
  code?: string | undefined;
  status?: number | undefined;
}

/**
 * Auth のエラーを分類する: セッションが使えない（未ログイン・ユーザー削除・トークン不正）なら null を返し、
 * 利用停止は AccountBannedError、それ以外（通信失敗・Auth の 5xx）は ApiError として投げる（React Query が再試行）。
 */
function sessionUnusable(error: AuthErrorLike): null {
  if (error.code === "user_banned") throw new AccountBannedError();
  if (error.name === "AuthSessionMissingError" || error.status === 401 || error.status === 403) {
    return null;
  }
  throw toAppError(error);
}

function fetchProfileRow(supabase: TypedSupabaseClient, userId: string) {
  return supabase
    .from("profiles")
    .select("id, display_name, deleted_at")
    .eq("id", userId)
    .maybeSingle();
}

/**
 * 自分のアカウント情報を取得する。未ログイン・セッション無効（ユーザー削除など）なら null。
 * 利用停止中（ban）なら AccountBannedError を投げる。
 *
 * getUser() は Auth サーバーに問い合わせてトークンを検証する（削除済み・利用停止中のユーザーも検知できる）。
 * 起動のたびに通る処理なので、端末に保存されたセッション（getSession: ネットワーク不要）のユーザー ID で
 * profiles の取得を同時に始め、往復 1 回分待ち時間を減らす。判定の正は getUser() の結果で、
 * getUser() が失敗したら profiles の結果は使わない（profiles は RLS で本人の行しか読めない）。
 * getUser() にはトークンを明示して渡す（引数なしだと Auth のロックを取ったまま通信するため、
 * 同時に始めた profiles の取得がそのロック待ちで結局直列になる）。
 *
 * 注意: 403 user_not_found / user_banned では supabase-js はこの端末のセッション（Cookie）を消さない。
 * 呼び出し側（AccountGuard）が必ず signOut してからログイン画面へ送ること（消さないと middleware が
 * まだ有効な JWT を見て / へ戻し、リダイレクトが無限に繰り返される）。
 */
export async function fetchMyAccount(): Promise<MyAccount | null> {
  const supabase = getSupabaseBrowserClient();
  // 期限切れ間近ならここでリフレッシュされる（通常はネットワーク不要）
  const { data: sessionData, error: sessionError } = await supabase.auth.getSession();
  if (sessionError) return sessionUnusable(sessionError);
  const session = sessionData.session;
  if (!session) return null;

  const [userResult, profileResult] = await Promise.all([
    supabase.auth.getUser(session.access_token),
    fetchProfileRow(supabase, session.user.id),
  ]);
  if (userResult.error) return sessionUnusable(userResult.error);
  const user = userResult.data.user;
  if (!user) return null;

  // 取得中に別のアカウントへ切り替わった（他タブでのログイン等）場合は、検証済みのユーザーで取り直す
  const { data, error } =
    user.id === session.user.id ? profileResult : await fetchProfileRow(supabase, user.id);
  if (error) throw toAppError(error);

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

/**
 * キャッシュ済みのアカウントとは別のユーザーでサインインしたか（別タブで別アカウントにログインした等）。
 * true なら前のユーザーのキャッシュ（メールアドレス・会話 ID・DM 本文など）を使い続けないよう破棄する。
 */
export function isAccountSwitched(
  cached: Pick<MyAccount, "userId"> | null | undefined,
  sessionUserId: string | null | undefined,
): boolean {
  return Boolean(cached && sessionUserId && cached.userId !== sessionUserId);
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
 *
 * 失敗の扱い:
 * - supabase-js はサーバーへの失効要求（/logout）が通信失敗・5xx でも、この端末のセッション（Cookie）を
 *   消してからエラーを返す。"local" でセッションがもう残っていなければ、この端末からはログアウト済みなので
 *   失敗とせずにログイン画面へ進む（「ログアウトできませんでした」と表示しながら実はログアウト済み、を防ぐ）。
 *   サーバー側のリフレッシュトークンは有効期限まで残るが、この端末にはもう無い。
 * - セッションが残っている（期限切れトークンの更新に失敗した等）なら、エラーを呼び出し元へ返す（握りつぶさない）。
 * - "global" の失敗は常に呼び出し元へ返す（全端末の無効化に失敗したことを呼び出し元が知る必要がある。
 *   退会画面は続けて "local" で呼び直し、この端末からは必ずログアウトさせる）。
 */
export async function signOutAndRedirect(
  options: { reason?: LoginErrorReason; scope?: "global" | "local"; next?: string } = {},
): Promise<{ error: Error | null }> {
  if (redirecting) return { error: null };
  redirecting = true;
  const scope = options.scope ?? "local";
  const supabase = getSupabaseBrowserClient();
  const { error } = await supabase.auth.signOut({ scope });
  if (error) {
    if (scope === "global" || (await hasLocalSession(supabase))) {
      redirecting = false;
      console.error("[auth] sign out failed:", error.message);
      return { error };
    }
    console.warn(
      "[auth] sign out request failed, but the session was removed from this device:",
      error.message,
    );
  }
  window.location.replace(loginPath({ error: options.reason, next: options.next }));
  return { error: null };
}

/** この端末にセッションが残っているか（確認できないときは残っているものとして扱う） */
async function hasLocalSession(supabase: TypedSupabaseClient): Promise<boolean> {
  try {
    const { data, error } = await supabase.auth.getSession();
    return Boolean(error) || data.session !== null;
  } catch {
    return true;
  }
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
