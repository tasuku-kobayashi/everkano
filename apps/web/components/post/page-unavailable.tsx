import Link from "next/link";

/** Instagram の「このページはご利用いただけません」（存在しない・非公開の投稿やプロフィール） */
export function PageUnavailable() {
  return (
    <div className="flex flex-col items-center px-8 pt-20 pb-14 text-center" role="alert">
      <h2 className="text-[18px] leading-6 font-bold text-balance">
        このページはご利用いただけません
      </h2>
      <p className="text-ig-secondary mt-3 max-w-[320px] text-[14px] leading-[18px] text-balance">
        リンクに問題があるか、ページが削除された可能性があります。
      </p>
      <Link href="/" className="text-ig-blue pressable mt-6 text-[14px] font-semibold">
        ホームに戻る
      </Link>
    </div>
  );
}
