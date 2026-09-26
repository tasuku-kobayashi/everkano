import { cn } from "@/lib/cn";
import { SparkleIcon } from "./icons";

/** バッジの文言（E3: UI には常に「AIキャラクター」バッジを表示する） */
export const AI_BADGE_LABEL = "AIキャラクター";

export interface AiBadgeProps {
  /**
   * full: 「✦ AIキャラクター」（名前・ハンドルの横）
   * compact: 「AI」だけ（ストーリーズのアバターの下端など、幅の無い所。読み上げは「AIキャラクター」）
   */
  variant?: "full" | "compact";
  className?: string;
}

/**
 * 「AIキャラクター」バッジ（E3。サクラサイト商法との区別のため、キャラが表示される所には常に付ける）。
 *
 * - Instagram の認証バッジ・「AI情報」ラベルと同じく、名前の横に小さく添える。キャラの名前を読み上げた直後に
 *   「AIキャラクター」と読み上げられるよう、見える文字をそのまま読ませる（compact は読み上げ用の文字を別に持つ）
 * - 色は --ig-ai-badge-*（ライト 6.3:1 / ダーク 7.6:1。WCAG AA）。背景色を持つので、どの面の上でも同じ見え方
 * - 行の幅が足りないときは名前の方を省略する（バッジは shrink-0）
 */
export function AiBadge({ variant = "full", className }: AiBadgeProps) {
  if (variant === "compact") {
    return (
      <span
        data-testid="ai-badge"
        className={cn(
          "inline-flex h-4 shrink-0 items-center rounded-[4px] bg-ig-ai-badge-bg px-1 text-[10px] leading-none font-bold tracking-[0.02em] text-ig-ai-badge-text",
          className,
        )}
      >
        <span aria-hidden="true">AI</span>
        <span className="sr-only">{AI_BADGE_LABEL}</span>
      </span>
    );
  }
  return (
    <span
      data-testid="ai-badge"
      className={cn(
        "inline-flex h-[18px] shrink-0 items-center gap-[3px] rounded-full bg-ig-ai-badge-bg pr-[7px] pl-[5px] align-middle text-[11px] leading-none font-bold whitespace-nowrap text-ig-ai-badge-text",
        className,
      )}
    >
      <SparkleIcon size={10} />
      {AI_BADGE_LABEL}
    </span>
  );
}
