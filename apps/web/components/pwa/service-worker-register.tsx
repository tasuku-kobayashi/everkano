"use client";

import { useEffect } from "react";
import { getPublicEnv } from "@/lib/env";

/**
 * Service Worker（public/sw.js）の登録。
 * - 本番ビルド、または NEXT_PUBLIC_ENABLE_SW=1 のときのみ登録する
 * - 開発中は古い SW がキャッシュを返して混乱しないよう、登録済みの SW を解除する
 * - 登録 URL にビルド ID を付ける（/sw.js?v=<ビルドID>）。sw.js 自体はデプロイ間で同一でも新しい SW が入り、
 *   オフラインページの取り直しと前のビルドのキャッシュ削除が行われる
 */
export function ServiceWorkerRegister() {
  useEffect(() => {
    if (!("serviceWorker" in navigator)) return;
    const enabled = process.env.NODE_ENV === "production" || getPublicEnv().enableServiceWorker;

    if (!enabled) {
      navigator.serviceWorker
        .getRegistrations()
        .then((registrations) => Promise.all(registrations.map((r) => r.unregister())))
        .catch((error: unknown) => console.warn("[sw] failed to unregister:", error));
      return;
    }

    const register = () => {
      navigator.serviceWorker
        // ビルドごとに URL が変わるので、デプロイのたびに新しい SW がインストールされる（public/sw.js の「バージョン」）
        .register(`/sw.js?v=${encodeURIComponent(getPublicEnv().buildId)}`, { scope: "/" })
        .catch((error: unknown) => console.error("[sw] registration failed:", error));
    };
    if (document.readyState === "complete") {
      register();
    } else {
      window.addEventListener("load", register, { once: true });
      return () => window.removeEventListener("load", register);
    }
  }, []);

  return null;
}
