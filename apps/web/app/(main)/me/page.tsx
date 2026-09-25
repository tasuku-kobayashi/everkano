import type { Metadata } from "next";
import { MeView } from "@/components/account/me-view";

export const metadata: Metadata = { title: "プロフィール" };

/** 自分のプロフィール（仕様 §5.7: メールアドレス表示・ログアウト・退会） */
export default function MePage() {
  return <MeView />;
}
