"use client";

import { useQueryClient } from "@tanstack/react-query";
import { useEffect } from "react";
import {
  isAccountSwitched,
  isRedirectingToLogin,
  signOutAndRedirect,
  useMyAccount,
  type MyAccount,
} from "@/lib/auth/account";
import { loginPath } from "@/lib/auth/redirect";
import { queryKeys } from "@/lib/queries/keys";
import { getSupabaseBrowserClient } from "@/lib/supabase/client";

/**
 * (main) レイアウトに 1 つだけ置くガード。
 * - 自分の profiles を 1 回確認し、退会済み（deleted_at あり）ならサインアウト → /login?error=withdrawn
 * - セッションが無効（ユーザー削除・リフレッシュ失敗・他タブでログアウト）ならログイン画面へ
 * - 他タブで別のアカウントにログインしたら読み込み直す（前のユーザーのキャッシュを表示し続けない）
 */
export function AccountGuard() {
  const queryClient = useQueryClient();
  const { data: account, isSuccess } = useMyAccount();

  useEffect(() => {
    if (!isSuccess) return;
    if (account === null) {
      window.location.replace(loginPath({ error: "session", next: window.location.pathname }));
      return;
    }
    if (account.deletedAt) {
      void signOutAndRedirect({ reason: "withdrawn" });
    } else if (!account.profileExists) {
      console.error("[auth] profile row not found for user", account.userId);
    }
  }, [account, isSuccess]);

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
