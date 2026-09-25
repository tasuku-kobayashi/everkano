"use client";

import { useQueryClient } from "@tanstack/react-query";
import { useEffect } from "react";
import {
  AccountBannedError,
  isAccountSwitched,
  isRedirectingToLogin,
  signOutAndRedirect,
  useMyAccount,
  type MyAccount,
} from "@/lib/auth/account";
import { clearPendingLogin } from "@/lib/auth/pending-login";
import { loginPath, type LoginErrorReason } from "@/lib/auth/redirect";
import { queryKeys } from "@/lib/queries/keys";
import { getSupabaseBrowserClient } from "@/lib/supabase/client";

/**
 * (main) レイアウトに 1 つだけ置くガード。
 * - 自分の profiles を 1 回確認し、退会済み（deleted_at あり）ならサインアウト → /login?error=withdrawn
 * - セッションが無効（ユーザー削除・リフレッシュ失敗・他タブでログアウト）ならサインアウト → /login?error=session
 * - 利用停止中（Supabase Auth の ban）ならサインアウト → /login?error=banned
 * - 他タブで別のアカウントにログインしたら読み込み直す（前のユーザーのキャッシュを表示し続けない）
 * - ログインできていれば、ログイン画面の「確認コード入力待ち」の保存状態を消す（マジックリンクでログインした場合、
 *   次にログイン画面を開いたときに古いコード入力画面が復元されないように）
 *
 * ログイン画面へ送る前に必ずこの端末のセッション（Cookie）を破棄する。残したままだと、JWT の期限内は
 * middleware が「ログイン済み」と判定して / へ戻し、/ ⇄ /login のリダイレクトが無限に繰り返される。
 */
export function AccountGuard() {
  const queryClient = useQueryClient();
  const { data: account, isSuccess, error } = useMyAccount();
  const banned = error instanceof AccountBannedError;

  useEffect(() => {
    if (banned) {
      void endSession("banned");
      return;
    }
    if (!isSuccess) return;
    if (account === null) {
      void endSession("session", window.location.pathname);
      return;
    }
    clearPendingLogin();
    if (account.deletedAt) {
      void signOutAndRedirect({ reason: "withdrawn" });
    } else if (!account.profileExists) {
      console.error("[auth] profile row not found for user", account.userId);
    }
  }, [account, isSuccess, banned]);

  useEffect(() => {
    const { data } = getSupabaseBrowserClient().auth.onAuthStateChange((event, session) => {
      if (event === "SIGNED_OUT" && !isRedirectingToLogin()) {
        window.location.replace(loginPath());
      } else if (
        event === "SIGNED_IN" &&
        isAccountSwitched(
          queryClient.getQueryData<MyAccount | null>(queryKeys.profile()),
          session?.user.id,
        )
      ) {
        // ハードナビゲーションでクライアント状態（React Query のキャッシュ）を完全に破棄する
        window.location.reload();
      }
    });
    return () => data.subscription.unsubscribe();
  }, [queryClient]);

  return null;
}

/**
 * この端末のセッションを破棄してログイン画面へ。サインアウトに失敗しても必ずログイン画面へ送る
 * （ログイン画面と middleware も ?error= を見てセッションを破棄・表示するため、ループにはならない）。
 */
async function endSession(reason: LoginErrorReason, next?: string): Promise<void> {
  if (isRedirectingToLogin()) return;
  const { error } = await signOutAndRedirect({ reason, scope: "local", next });
  if (error) window.location.replace(loginPath({ error: reason, next }));
}
