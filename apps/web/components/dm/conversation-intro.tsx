"use client";

import type { PublicCharacter } from "@everkano/shared";
import { useQueryClient } from "@tanstack/react-query";
import Link from "next/link";
import { AiBadge } from "@/components/ui/ai-badge";
import { Avatar } from "@/components/ui/avatar";
import { buttonClassName } from "@/components/ui/button";
import { formatCount } from "@/lib/format";
import { prefetchCharacterProfile } from "@/lib/queries/prefetch";

export interface ConversationIntroProps {
  character: Pick<PublicCharacter, "name" | "handle" | "avatar_url" | "follower_count"> | null;
}

/** 会話の先頭（過去ログを遡りきった所）に出すプロフィールカード（Instagram DM と同じ） */
export function ConversationIntro({ character }: ConversationIntroProps) {
  const queryClient = useQueryClient();
  if (!character) return <div className="h-6" />;
  return (
    <section
      aria-label={`${character.name}のプロフィール`}
      className="flex flex-col items-center px-6 pt-8 pb-6 text-center"
    >
      <Avatar src={character.avatar_url} alt={character.name} size={96} />
      <h2 className="mt-3 text-[20px] leading-6 font-bold">{character.name}</h2>
      <AiBadge className="mt-1.5" />
      <p className="mt-1 text-[14px] leading-[18px] text-ig-secondary">
        {character.handle} · everkano
      </p>
      {character.follower_count > 0 ? (
        <p className="text-[14px] leading-[18px] text-ig-secondary">
          フォロワー{formatCount(character.follower_count)}人
        </p>
      ) : null}
      <Link
        href={`/c/${encodeURIComponent(character.handle)}`}
        // リンクに触れた時点でプロフィールのデータを取りに行く（lib/queries/prefetch.ts）
        onPointerDown={() => prefetchCharacterProfile(queryClient, character.handle)}
        className={buttonClassName({ variant: "secondary", size: "sm" }) + " mt-4"}
      >
        プロフィールを見る
      </Link>
    </section>
  );
}
