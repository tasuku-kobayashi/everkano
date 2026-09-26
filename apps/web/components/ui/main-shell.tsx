"use client";

import { usePathname } from "next/navigation";
import type { ReactNode } from "react";
import { cn } from "@/lib/cn";
import { useHomeScrollMemory } from "@/lib/home-scroll";
import { HomeFeedRefresher } from "./pull-to-refresh";
import { isTabBarHiddenPath, TabBar } from "./tab-bar";

/**
 * ログイン後画面の外枠（スマホ幅固定 max-w 480px・中央寄せ・タブバー）。
 * タブバーを表示する画面では、本文の下にタブバー分（+ safe-area）の余白を空ける。
 * ホーム（/）ではフィードの再読み込み（プルリフレッシュ・復帰時の更新）を有効にする。
 * ホームのスクロール位置を覚えておき、タブバーの Home タブで戻ったときに復元する（lib/home-scroll.ts）。
 * 本文は <main>（ランドマーク）にし、タブバー（<nav>）はその外に置く。スクリーンリーダーの「メイン」への移動用。
 * (main) 配下の画面は <main> を自前で出さないこと（入れ子にしない）。
 */
export function MainShell({ children }: { children: ReactNode }) {
  const pathname = usePathname();
  useHomeScrollMemory(pathname);
  const showTabBar = !isTabBarHiddenPath(pathname);
  return (
    <div className="relative mx-auto flex min-h-dvh w-full max-w-[480px] flex-col bg-ig-bg">
      <main id="main" className={cn("flex flex-1 flex-col", showTabBar && "pb-tabbar")}>
        <RouteContent>{children}</RouteContent>
      </main>
      {pathname === "/" ? <HomeFeedRefresher /> : null}
      <TabBar />
    </div>
  );
}

/**
 * レイアウトから渡された children（RSC）をそのまま返すだけの境界。ハイドレーションエラー（React #418）の回避。
 *
 * children にはサーバーから分割して届く未解決の要素（RSC の lazy 参照）が含まれることがある。<main> のような
 * ホスト要素の直下でそれを解決すると、ハイドレーション中に中断（suspend）→ 再開（replay）したとき、React
 * （Next.js 15.5 同梱の 19.2 canary）は <main> を作り直す際にハイドレーションの位置を戻さず、<main> を
 * その最初の子（<!--$-->）に対応付けようとして不一致になる（その場合 React はルート全体をクライアントで描画し直す）。
 * 負荷が高く RSC の到着とハイドレーションが重なったときだけ起きていた（/dm・/dm/[characterId] など）。
 * 関数コンポーネントで挟むと、中断・再開はこのコンポーネントで起き、DOM の対応付けの位置はずれない。
 */
function RouteContent({ children }: { children: ReactNode }) {
  return children;
}
