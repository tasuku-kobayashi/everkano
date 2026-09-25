import type { ReactNode } from "react";
import { AccountGuard } from "@/components/auth/account-guard";
import { MainShell } from "@/components/ui/main-shell";

/** ログイン後の画面（ホーム・検索・DM・プロフィール・投稿・キャラ）の共通レイアウト */
export default function MainLayout({ children }: { children: ReactNode }) {
  return (
    <MainShell>
      <AccountGuard />
      {children}
    </MainShell>
  );
}
