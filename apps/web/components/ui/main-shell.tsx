"use client";

import { usePathname } from "next/navigation";
import type { ReactNode } from "react";
import { cn } from "@/lib/cn";
import { isTabBarHiddenPath, TabBar } from "./tab-bar";

/**
 * ログイン後画面の外枠（スマホ幅固定 max-w 480px・中央寄せ・タブバー）。
 * タブバーを表示する画面では、本文の下にタブバー分（+ safe-area）の余白を空ける。
 */
export function MainShell({ children }: { children: ReactNode }) {
  const pathname = usePathname();
  const showTabBar = !isTabBarHiddenPath(pathname);
  return (
    <div className="bg-ig-bg relative mx-auto flex min-h-dvh w-full max-w-[480px] flex-col">
      <div className={cn("flex flex-1 flex-col", showTabBar && "pb-tabbar")}>{children}</div>
      <TabBar />
    </div>
  );
}
