"use client";

import { useQuery } from "@tanstack/react-query";
import Link from "next/link";
import { usePathname } from "next/navigation";
import { useEffect, type ReactNode } from "react";
import { accountDisplayName, useMyAccount } from "@/lib/auth/account";
import { cn } from "@/lib/cn";
import { queryKeys } from "@/lib/queries/keys";
import { getSupabaseBrowserClient } from "@/lib/supabase/client";
import {
  HomeFilledIcon,
  HomeIcon,
  PaperPlaneFilledIcon,
  PaperPlaneIcon,
  SearchBoldIcon,
  SearchIcon,
} from "./icons";

type TabKey = "home" | "search" | "dm" | "me";

/** タブバーを出さない画面（固定の入力フッターを持つ画面） */
const HIDDEN_PATTERNS = [/^\/posts\/[^/]+\/?$/, /^\/dm\/[^/]+\/?$/];

export function isTabBarHiddenPath(pathname: string): boolean {
  return HIDDEN_PATTERNS.some((pattern) => pattern.test(pathname));
}

/** パスに直接対応するタブ（/c/... など対応しないパスは null） */
function tabForPath(pathname: string): TabKey | null {
  if (pathname === "/") return "home";
  if (pathname === "/search" || pathname.startsWith("/search/")) return "search";
  if (pathname === "/dm" || pathname.startsWith("/dm/")) return "dm";
  if (pathname === "/me" || pathname.startsWith("/me/")) return "me";
  return null;
}

// キャラプロフィール等（どのタブにも属さない画面）では、直前にいたタブをアクティブのままにする（Instagram と同じ挙動）
let lastTab: TabKey = "home";

/** タブバーの DM 未読バッジ（list_dm_threads の unread_count 合計）。DM 側は queryKeys.dm() を invalidate すれば更新される */
function useDmUnreadTotal(): number {
  const { data } = useQuery({
    queryKey: queryKeys.dmUnreadTotal(),
    queryFn: async () => {
      const { data: threads, error } = await getSupabaseBrowserClient().rpc("list_dm_threads");
      if (error) throw error;
      return (threads ?? []).reduce((sum, thread) => sum + (thread.unread_count ?? 0), 0);
    },
    staleTime: 30_000,
    refetchInterval: 60_000,
    refetchOnWindowFocus: true,
  });
  return data ?? 0;
}

/**
 * 下部タブバー（4 タブ: ホーム / 検索 / DM / プロフィール。投稿タブは存在しない = H2）。
 * /posts/[postId] と /dm/[characterId] では非表示。
 */
export function TabBar() {
  const pathname = usePathname();
  const direct = tabForPath(pathname);
  const active: TabKey = direct ?? lastTab;
  const unread = useDmUnreadTotal();
  const { data: account } = useMyAccount();

  useEffect(() => {
    if (direct) lastTab = direct;
  }, [direct]);

  if (isTabBarHiddenPath(pathname)) return null;

  const displayName = accountDisplayName(account);

  return (
    <nav
      aria-label="メインメニュー"
      className="fixed inset-x-0 bottom-0 z-40 mx-auto max-w-[480px] border-t border-ig-separator bg-ig-bg pb-safe"
    >
      <ul className="flex h-[var(--tab-bar-h)] items-stretch">
        <Tab href="/" label="ホーム" active={active === "home"} pathname={pathname}>
          {active === "home" ? <HomeFilledIcon size={26} /> : <HomeIcon size={26} />}
        </Tab>
        <Tab href="/search" label="検索" active={active === "search"} pathname={pathname}>
          {active === "search" ? <SearchBoldIcon size={26} /> : <SearchIcon size={26} />}
        </Tab>
        <Tab
          href="/dm"
          label={unread > 0 ? `メッセージ（未読 ${unread} 件）` : "メッセージ"}
          active={active === "dm"}
          pathname={pathname}
        >
          <span className="relative">
            {active === "dm" ? <PaperPlaneFilledIcon size={26} /> : <PaperPlaneIcon size={26} />}
            {unread > 0 ? (
              <span className="absolute -top-1.5 -right-2 flex h-[18px] min-w-[18px] items-center justify-center rounded-full border-2 border-ig-bg bg-ig-badge px-1 text-[11px] leading-none font-bold text-white">
                {unread > 9 ? "9+" : unread}
              </span>
            ) : null}
          </span>
        </Tab>
        <Tab href="/me" label="プロフィール" active={active === "me"} pathname={pathname}>
          <span
            className={cn(
              "flex size-[28px] items-center justify-center rounded-full",
              active === "me" ? "ring-2 ring-ig-text" : "ring-1 ring-ig-separator",
            )}
          >
            <span
              className={cn(
                "flex size-[24px] items-center justify-center rounded-full text-[12px] leading-none font-bold text-white",
                displayName ? "brand-gradient" : "bg-ig-elevated",
              )}
            >
              {displayName.charAt(0).toUpperCase()}
            </span>
          </span>
        </Tab>
      </ul>
    </nav>
  );
}

function Tab({
  href,
  label,
  active,
  pathname,
  children,
}: {
  href: string;
  label: string;
  active: boolean;
  pathname: string;
  children: ReactNode;
}) {
  return (
    <li className="flex flex-1">
      <Link
        href={href}
        aria-label={label}
        aria-current={active ? "page" : undefined}
        onClick={() => {
          // 表示中のタブをもう一度タップしたら先頭へスクロール（Instagram と同じ）
          if (pathname === href) window.scrollTo({ top: 0, behavior: "smooth" });
        }}
        className="flex flex-1 items-center justify-center text-ig-text transition-transform active:scale-90"
      >
        {children}
      </Link>
    </li>
  );
}
