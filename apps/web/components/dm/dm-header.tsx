"use client";

import type { PublicCharacter } from "@everkano/shared";
import Link from "next/link";
import { AppHeader, HeaderIconButton } from "@/components/ui/app-header";
import { Avatar } from "@/components/ui/avatar";
import { InfoIcon } from "@/components/ui/icons";
import { Skeleton } from "@/components/ui/skeleton";

export interface DmHeaderProps {
  character: Pick<PublicCharacter, "name" | "handle" | "avatar_url"> | null | undefined;
  /** 「i」ボタン（メモリパネル）。未指定なら出さない */
  onOpenInfo?: () => void;
}

/**
 * DM 会話のヘッダー: 「<」戻る / アバター（アクティブの緑の点）+ 名前 + 「アクティブ中」/ 右に「i」。
 * アバターと名前はキャラのプロフィールへのリンク。
 */
export function DmHeader({ character, onOpenInfo }: DmHeaderProps) {
  return (
    <AppHeader
      variant="back"
      backHref="/dm"
      bordered
      right={
        onOpenInfo && character ? (
          <HeaderIconButton label={`${character.name}が覚えていること`} onClick={onOpenInfo}>
            <InfoIcon size={26} strokeWidth={1.8} />
          </HeaderIconButton>
        ) : undefined
      }
    >
      {character ? (
        <Link
          href={`/c/${encodeURIComponent(character.handle)}`}
          className="pressable flex min-w-0 items-center gap-2.5"
          aria-label={`${character.name}のプロフィール`}
        >
          <span className="relative shrink-0">
            <Avatar src={character.avatar_url} alt={character.name} size={28} />
            <span
              aria-hidden="true"
              className="border-ig-bg absolute -right-0.5 -bottom-0.5 size-[11px] rounded-full border-2 bg-[#1cd14f]"
            />
          </span>
          <span className="flex min-w-0 flex-col">
            <span className="truncate text-[16px] leading-5 font-bold">{character.name}</span>
            <span className="text-ig-secondary truncate text-[12px] leading-4">アクティブ中</span>
          </span>
        </Link>
      ) : character === null ? null : (
        <span className="flex items-center gap-2.5" aria-hidden="true">
          <Skeleton shape="circle" className="size-7" />
          <span className="flex flex-col gap-1.5">
            <Skeleton shape="text" className="w-20" />
            <Skeleton shape="text" className="h-2 w-14" />
          </span>
        </span>
      )}
    </AppHeader>
  );
}
