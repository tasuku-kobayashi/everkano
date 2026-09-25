import { Avatar } from "@/components/ui/avatar";

export interface TypingIndicatorProps {
  avatarUrl: string | null | undefined;
  name: string;
}

/** キャラの「入力中…」（3 つの点が順に跳ねる受信側の吹き出し） */
export function TypingIndicator({ avatarUrl, name }: TypingIndicatorProps) {
  return (
    <div
      className="mt-2 flex animate-fade-in items-end gap-2 pr-16 pl-3"
      role="status"
      aria-label={`${name}が入力中`}
    >
      <Avatar src={avatarUrl} alt={name} size={28} />
      <div className="flex h-[38px] items-center gap-[5px] rounded-[22px] bg-ig-elevated px-4">
        {[0, 1, 2].map((i) => (
          <span
            key={i}
            aria-hidden="true"
            className="size-[7px] animate-typing-dot rounded-full bg-ig-secondary"
            style={{ animationDelay: `${i * 0.18}s` }}
          />
        ))}
      </div>
    </div>
  );
}
