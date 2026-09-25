import { cn } from "@/lib/cn";

export interface SpinnerProps {
  /** px。既定 20 */
  size?: number;
  className?: string;
  /** スクリーンリーダー向けラベル。既定「読み込み中」 */
  label?: string;
}

const BARS = Array.from({ length: 8 }, (_, i) => i);

/** Instagram の放射状ローディング（8 本のバーが段階的に回転） */
export function Spinner({ size = 20, className, label = "読み込み中" }: SpinnerProps) {
  return (
    <span role="status" aria-label={label} className={cn("inline-flex", className)}>
      <svg
        width={size}
        height={size}
        viewBox="0 0 100 100"
        className="animate-ig-spinner"
        aria-hidden="true"
      >
        {BARS.map((i) => (
          <rect
            key={i}
            x="44"
            y="6"
            width="12"
            height="28"
            rx="6"
            fill="currentColor"
            opacity={0.25 + (i / 8) * 0.75}
            transform={`rotate(${i * 45} 50 50)`}
          />
        ))}
      </svg>
    </span>
  );
}

/** 画面中央に大きめのスピナー */
export function PageSpinner({ className }: { className?: string }) {
  return (
    <div className={cn("flex w-full justify-center py-10 text-ig-secondary", className)}>
      <Spinner size={28} />
    </div>
  );
}
