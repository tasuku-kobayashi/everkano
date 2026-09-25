"use client";

import { useEffect } from "react";
import { isRedirectingToLogin, signOutAndRedirect, useMyAccount } from "@/lib/auth/account";
import { loginPath } from "@/lib/auth/redirect";
import { getSupabaseBrowserClient } from "@/lib/supabase/client";

/**
 * (main) レイアウトに 1 つだけ置くガード。
 * - 自分の profiles を 1 回確認し、退会済み（deleted_at あり）ならサインアウト → /login?error=withdrawn
 * - セッションが無効（ユーザー削除・リフレッシュ失敗・他タブでログアウト）ならログイン画面へ
 */
export function AccountGuard() {
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
    const { data } = getSupabaseBrowserClient().auth.onAuthStateChange((event) => {
      if (event === "SIGNED_OUT" && !isRedirectingToLogin()) {
        window.location.replace(loginPath());
      }
    });
    return () => data.subscription.unsubscribe();
  }, []);

  return null;
}
