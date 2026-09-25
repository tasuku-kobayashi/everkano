import Link from "next/link";

export interface PageUnavailableProps {
  /**
   * 見出しのレベル。ヘッダーに h1 が無い画面（見つからないプロフィール）では 1 にして画面の見出しにする。
   * 既定 2（投稿詳細はヘッダーの「投稿」が h1）
   */
  headingLevel?: 1 | 2;
}

/** Instagram の「このページはご利用いただけません」（存在しない・非公開の投稿やプロフィール） */
export function PageUnavailable({ headingLevel = 2 }: PageUnavailableProps) {
  const Heading = headingLevel === 1 ? "h1" : "h2";
  return (
    <div className="flex flex-col items-center px-8 pt-20 pb-14 text-center" role="alert">
      <Heading className="text-[18px] leading-6 font-bold text-balance [word-break:auto-phrase]">
        このページはご利用いただけません
      </Heading>
      <p className="mt-3 max-w-[320px] text-[14px] leading-[18px] text-balance [word-break:auto-phrase] text-ig-secondary">
        リンクに問題があるか、ページが削除された可能性があります。
      </p>
      <Link href="/" className="mt-6 text-[14px] font-semibold text-ig-blue-text pressable">
        ホームに戻る
      </Link>
    </div>
  );
}
