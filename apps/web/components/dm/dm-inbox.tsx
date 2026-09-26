"use client";

import type { DmThread, PublicCharacter } from "@everkano/shared";
import { useQueryClient } from "@tanstack/react-query";
import Link from "next/link";
import { useMemo, useState } from "react";
import { OPAQUE_HEADER } from "@/components/post/opaque-header";
import { AppHeader } from "@/components/ui/app-header";
import { AI_BADGE_LABEL, AiBadge } from "@/components/ui/ai-badge";
import { Avatar } from "@/components/ui/avatar";
import { ErrorState } from "@/components/ui/error-state";
import { CloseIcon, PaperPlaneIcon, SearchIcon } from "@/components/ui/icons";
import { ListRowSkeleton } from "@/components/ui/skeleton";
import { getErrorMessage } from "@/lib/api/errors";
import { accountDisplayName, useMyAccount } from "@/lib/auth/account";
import { cn } from "@/lib/cn";
import { formatRelativeTimeShort } from "@/lib/format";
import {
  excludeConversed,
  filterThreads,
  prefetchDmConversation,
  threadPreview,
  useDmThreads,
  useDmThreadsRealtime,
  useSuggestedCharacters,
} from "@/lib/queries/dm";

/**
 * DM 一覧（/dm、仕様 §5.5 / C-1）。
 * 会話済みキャラ（最新メッセージ・時刻・未読）と、まだ話していないキャラの「おすすめ」。
 */
export function DmInbox() {
  const queryClient = useQueryClient();
  const { data: account } = useMyAccount();
  const threadsQuery = useDmThreads();
  const suggestionsQuery = useSuggestedCharacters();
  const [query, setQuery] = useState("");

  const threads = useMemo(() => threadsQuery.data ?? [], [threadsQuery.data]);
  const conversationIds = useMemo(() => threads.map((t) => t.conversation_id), [threads]);
  useDmThreadsRealtime(conversationIds);
  const visibleThreads = useMemo(() => filterThreads(threads, query), [threads, query]);
  const suggestions = useMemo(() => {
    const candidates = excludeConversed(suggestionsQuery.data ?? [], threads);
    const q = query.trim().toLowerCase();
    return q
      ? candidates.filter(
          (c) => c.name.toLowerCase().includes(q) || c.handle.toLowerCase().includes(q),
        )
      : candidates;
  }, [suggestionsQuery.data, threads, query]);

  const title = account ? accountDisplayName(account) : "メッセージ";
  const loadingThreads = threadsQuery.isPending;
  const now = new Date();

  return (
    <>
      <AppHeader variant="title" title={title} className={OPAQUE_HEADER} />

      <div className="px-4 pt-1 pb-3">
        <label className="flex h-9 items-center gap-2 rounded-[10px] bg-ig-elevated px-3">
          <SearchIcon size={16} strokeWidth={2.2} className="shrink-0 text-ig-secondary" />
          <span className="sr-only">メッセージを検索</span>
          <input
            type="search"
            value={query}
            onChange={(event) => setQuery(event.target.value)}
            placeholder="検索"
            enterKeyHint="search"
            className="min-w-0 flex-1 bg-transparent text-[16px] leading-5 outline-none placeholder:text-ig-secondary [&::-webkit-search-cancel-button]:hidden"
          />
          {query ? (
            <button
              type="button"
              aria-label="検索をクリア"
              onClick={() => setQuery("")}
              className="shrink-0 text-ig-secondary"
            >
              <CloseIcon size={14} strokeWidth={2.6} />
            </button>
          ) : null}
        </label>
      </div>

      <section aria-labelledby="dm-threads-heading">
        <div className="flex items-center justify-between px-4 pt-1 pb-2">
          <h2 id="dm-threads-heading" className="text-[16px] leading-5 font-bold">
            メッセージ
          </h2>
        </div>

        {loadingThreads ? (
          <div aria-busy="true" aria-label="読み込み中">
            {Array.from({ length: 5 }, (_, i) => (
              <ListRowSkeleton key={i} />
            ))}
          </div>
        ) : threadsQuery.isError && !threadsQuery.data ? (
          <ErrorState
            message={getErrorMessage(threadsQuery.error)}
            onRetry={() => void threadsQuery.refetch()}
            retrying={threadsQuery.isRefetching}
          />
        ) : threads.length === 0 ? (
          <div className="flex flex-col items-center px-8 pt-6 pb-8 text-center">
            <div className="mb-4 flex size-[62px] items-center justify-center rounded-full border-2 border-ig-text">
              <PaperPlaneIcon size={30} strokeWidth={1.6} />
            </div>
            <p className="text-[18px] leading-6 font-bold">メッセージはまだありません</p>
            <p className="mt-1.5 text-[14px] leading-[18px] text-ig-secondary">
              気になるキャラクターにメッセージを送ってみよう。
            </p>
          </div>
        ) : visibleThreads.length === 0 ? (
          <p className="px-4 py-6 text-center text-[14px] text-ig-secondary">
            「{query.trim()}」に一致する会話はありません
          </p>
        ) : (
          <ul>
            {visibleThreads.map((thread) => (
              <li key={thread.conversation_id}>
                <ThreadRow
                  thread={thread}
                  now={now}
                  onIntent={() => prefetchDmConversation(queryClient, thread.character_id)}
                />
              </li>
            ))}
          </ul>
        )}
      </section>

      {suggestions.length > 0 && !loadingThreads ? (
        <section aria-labelledby="dm-suggestions-heading" className="pt-4 pb-4">
          <h2 id="dm-suggestions-heading" className="px-4 pb-2 text-[16px] leading-5 font-bold">
            おすすめ
          </h2>
          <ul>
            {suggestions.map((character) => (
              <li key={character.id}>
                <SuggestionRow
                  character={character}
                  onIntent={() => prefetchDmConversation(queryClient, character.id)}
                />
              </li>
            ))}
          </ul>
        </section>
      ) : suggestionsQuery.isError && !suggestionsQuery.data ? (
        <p className="px-4 py-4 text-center text-[13px] text-ig-secondary">
          おすすめを読み込めませんでした
        </p>
      ) : null}
    </>
  );
}

function ThreadRow({
  thread,
  now,
  onIntent,
}: {
  thread: DmThread;
  now: Date;
  /** タップし始めた・ポインタが乗った（遷移より先に会話のデータを取りに行く） */
  onIntent: () => void;
}) {
  const unread = thread.unread_count > 0;
  const time = formatRelativeTimeShort(thread.last_message_at, now);
  const preview = threadPreview(thread);
  const label = [
    thread.character_name,
    AI_BADGE_LABEL,
    unread ? `未読 ${thread.unread_count} 件` : null,
    preview,
    time,
  ]
    .filter(Boolean)
    .join("、");

  return (
    <Link
      href={`/dm/${thread.character_id}`}
      // 会話済みのキャラは数が限られ（キャラごとに 1 会話）開く可能性が高いため、画面（RSC とページの JS）も
      // 丸ごと先読みしておく。データはタップし始めた時点で onIntent が取りに行く
      prefetch
      onPointerDown={onIntent}
      onMouseEnter={onIntent}
      aria-label={label}
      className="flex items-center gap-3 px-4 py-2 transition-colors active:bg-ig-elevated"
    >
      <Avatar src={thread.character_avatar_url} alt={thread.character_name} size="lg" />
      <div className="min-w-0 flex-1">
        <p className="flex min-w-0 items-center gap-1.5">
          <span
            className={cn("min-w-0 truncate text-[14px] leading-[18px]", unread && "font-bold")}
          >
            {thread.character_name}
          </span>
          <AiBadge />
        </p>
        <p
          className={cn(
            "flex min-w-0 text-[14px] leading-[18px]",
            unread ? "font-bold text-ig-text" : "text-ig-secondary",
          )}
        >
          <span className="truncate">
            {unread && thread.unread_count > 1
              ? `新しいメッセージ${thread.unread_count > 9 ? "9+" : thread.unread_count}件`
              : preview}
          </span>
          <span className="shrink-0 font-normal whitespace-pre">
            {" · "}
            {time}
          </span>
        </p>
      </div>
      {unread ? (
        <span aria-hidden="true" className="size-2 shrink-0 rounded-full bg-ig-blue" />
      ) : null}
    </Link>
  );
}

function SuggestionRow({
  character,
  onIntent,
}: {
  character: PublicCharacter;
  /** タップし始めた・ポインタが乗った（遷移より先に会話画面のキャラ情報を取りに行く。会話の作成はしない） */
  onIntent: () => void;
}) {
  return (
    <Link
      href={`/dm/${character.id}`}
      onPointerDown={onIntent}
      onMouseEnter={onIntent}
      aria-label={`${character.name}にメッセージを送る（${AI_BADGE_LABEL}）`}
      className="flex items-center gap-3 px-4 py-2 transition-colors active:bg-ig-elevated"
    >
      <Avatar src={character.avatar_url} alt={character.name} size="lg" />
      <div className="min-w-0 flex-1">
        <p className="flex min-w-0 items-center gap-1.5">
          <span className="min-w-0 truncate text-[14px] leading-[18px] font-semibold">
            {character.name}
          </span>
          <AiBadge />
        </p>
        <p className="truncate text-[14px] leading-[18px] text-ig-secondary">{character.handle}</p>
      </div>
      <span className="flex h-8 shrink-0 items-center rounded-lg bg-ig-elevated px-3 text-[14px] font-semibold text-ig-text">
        メッセージ
      </span>
    </Link>
  );
}
