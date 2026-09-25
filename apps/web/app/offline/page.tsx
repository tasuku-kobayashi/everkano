import type { Metadata } from "next";
import { WifiOffIcon } from "@/components/ui/icons";
import { ReloadButton } from "./reload-button";

export const metadata: Metadata = { title: "オフライン" };
export const dynamic = "force-static";

/** Service Worker がオフライン時に返すページ（sw.js が事前キャッシュする） */
export default function OfflinePage() {
  return (
    <main className="mx-auto flex min-h-dvh w-full max-w-[480px] flex-col items-center justify-center px-8 pt-safe pb-safe text-center">
      <div className="mb-4 flex size-[72px] items-center justify-center rounded-full border-2 border-ig-text">
        <WifiOffIcon size={36} strokeWidth={1.6} />
      </div>
      <h1 className="text-[22px] leading-7 font-bold">オフラインです</h1>
      <p className="mt-2 text-[14px] leading-[18px] text-ig-secondary">
        インターネットに接続されていません。
        <br />
        接続を確認して、再度お試しください。
      </p>
      <ReloadButton />
    </main>
  );
}
