import { Avatar } from "@/components/ui/avatar";

export interface TypingIndicatorProps {
  avatarUrl: string | null | undefined;
  name: string;
}

/** キャラの「入力中…」（3 つの点が順に跳ねる受信側の吹き出し） */
export function TypingIndicator({ avatarUrl, name }: TypingIndicatorProps) {
  return (
    <div
      className="animate-fade-in mt-2 flex items-end gap-2 pr-16 pl-3"
      role="status"
      aria-label={`${name}が入力中`}
    >
      <Avatar src={avatarUrl} alt={name} size={28} />
      <div className="bg-ig-elevated flex h-[38px] items-center gap-[5px] rounded-[22px] px-4">
        {[0, 1, 2].map((i) => (
          <span
            key={i}
            aria-hidden="true"
            className="animate-typing-dot bg-ig-secondary size-[7px] rounded-full"
            style={{ animationDelay: `${i * 0.18}s` }}
          />
        ))}
      </div>
    </div>
  );
}
