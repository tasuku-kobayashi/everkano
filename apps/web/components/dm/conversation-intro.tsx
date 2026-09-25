import type { PublicCharacter } from "@everkano/shared";
import Link from "next/link";
import { Avatar } from "@/components/ui/avatar";
import { buttonClassName } from "@/components/ui/button";
import { formatCount } from "@/lib/format";

export interface ConversationIntroProps {
  character: Pick<PublicCharacter, "name" | "handle" | "avatar_url" | "follower_count"> | null;
}

/** 会話の先頭（過去ログを遡りきった所）に出すプロフィールカード（Instagram DM と同じ） */
export function ConversationIntro({ character }: ConversationIntroProps) {
  if (!character) return <div className="h-6" />;
  return (
    <section
      aria-label={`${character.name}のプロフィール`}
      className="flex flex-col items-center px-6 pt-8 pb-6 text-center"
    >
      <Avatar src={character.avatar_url} alt={character.name} size={96} />
      <h2 className="mt-3 text-[20px] leading-6 font-bold">{character.name}</h2>
      <p className="text-ig-secondary mt-1 text-[14px] leading-[18px]">
        {character.handle} · everkano
      </p>
      {character.follower_count > 0 ? (
        <p className="text-ig-secondary text-[14px] leading-[18px]">
          フォロワー{formatCount(character.follower_count)}人
        </p>
      ) : null}
      <Link
        href={`/c/${encodeURIComponent(character.handle)}`}
        className={buttonClassName({ variant: "secondary", size: "sm" }) + " mt-4"}
      >
        プロフィールを見る
      </Link>
    </section>
  );
}
