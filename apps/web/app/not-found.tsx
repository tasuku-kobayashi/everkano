import Link from "next/link";
import { buttonClassName } from "@/components/ui/button";

export default function NotFound() {
  return (
    <main className="mx-auto flex min-h-dvh w-full max-w-[480px] flex-col items-center justify-center px-8 pt-safe text-center">
      <h1 className="text-[22px] leading-7 font-bold">このページはご利用いただけません</h1>
      <p className="mt-3 text-[14px] leading-[18px] text-ig-secondary">
        リンクに問題があるか、ページが削除された可能性があります。
      </p>
      <Link href="/" className={buttonClassName({ variant: "primary", size: "md" }) + " mt-6"}>
        ホームに戻る
      </Link>
    </main>
  );
}
