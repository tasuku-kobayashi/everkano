"use client";

import type { PublicCharacter } from "@everkano/shared";
import { useQueryClient } from "@tanstack/react-query";
import Link from "next/link";
import { OPAQUE_HEADER } from "@/components/post/opaque-header";
import { AI_BADGE_LABEL, AiBadge } from "@/components/ui/ai-badge";
import { AppHeader, HeaderIconButton } from "@/components/ui/app-header";
import { Avatar } from "@/components/ui/avatar";
import { InfoIcon } from "@/components/ui/icons";
import { Skeleton } from "@/components/ui/skeleton";
import { showsActiveDot, statusLine, useCharacterState } from "@/lib/queries/character-state";
import { prefetchCharacterProfile } from "@/lib/queries/prefetch";

export interface DmHeaderProps {
  character: Pick<PublicCharacter, "id" | "name" | "handle" | "avatar_url"> | null | undefined;
  /** 「i」ボタン（メモリパネル）。未指定なら出さない */
  onOpenInfo?: () => void;
}

/**
 * DM 会話のヘッダー: 「<」戻る / アバター（アクティブの緑の点）+ 名前 +「AIキャラクター」バッジ（E3）+
 * 今の状況（character_states.status_label。無ければ「アクティブ」）/ 右に「i」。
 * アバターと名前はキャラのプロフィールへのリンク。好感度・関係の段階は表示しない（A11）。
 */
export function DmHeader({ character, onOpenInfo }: DmHeaderProps) {
  const queryClient = useQueryClient();
  const stateQuery = useCharacterState(character?.id ?? "", Boolean(character?.id));
  const state = stateQuery.data;
  // 読み込み中は空（「アクティブ」→「仕事中」のように表示が切り替わって見えないように）。
  // 行が無い・読み込めなかったときは「アクティブ」
  const status = stateQuery.isPending ? "" : statusLine(state);
  return (
    <AppHeader
      variant="back"
      backHref="/dm"
      bordered
      // 画面の見出し（視覚的には非表示の h1）= キャラクター名
      heading={character?.name}
      // 半透明だとスクロールした吹き出し・時刻の区切りが名前や状況の下に透けて読みにくい
      className={OPAQUE_HEADER}
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
          // リンクに触れた時点でプロフィールのデータを取りに行く（lib/queries/prefetch.ts）
          onPointerDown={() => prefetchCharacterProfile(queryClient, character.handle)}
          className="flex min-w-0 items-center gap-2.5 pressable"
          aria-label={`${character.name}のプロフィール（${AI_BADGE_LABEL}${status ? `・${status}` : ""}）`}
        >
          <span className="relative shrink-0">
            <Avatar src={character.avatar_url} alt={character.name} size={28} />
            {showsActiveDot(state) ? (
              <span
                aria-hidden="true"
                className="absolute -right-0.5 -bottom-0.5 size-[11px] rounded-full border-2 border-ig-bg bg-[#1cd14f]"
              />
            ) : null}
          </span>
          <span className="flex min-w-0 flex-col">
            <span className="flex min-w-0 items-center gap-1.5">
              <span className="truncate text-[16px] leading-5 font-bold">{character.name}</span>
              <AiBadge />
            </span>
            <span
              className="truncate text-[12px] leading-4 text-ig-secondary"
              data-testid="dm-status"
            >
              {status || "\u00a0"}
            </span>
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
