"use client";

import type { ChatResponse, MessageDTO, PublicCharacter } from "@everkano/shared";
import { useQueryClient } from "@tanstack/react-query";
import Link from "next/link";
import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { MemoryPanel } from "@/components/memory/memory-panel";
import { buttonClassName } from "@/components/ui/button";
import { EmptyState } from "@/components/ui/empty-state";
import { ErrorState } from "@/components/ui/error-state";
import { BookmarkIcon, ChevronDownIcon, UserIcon } from "@/components/ui/icons";
import { InfiniteScrollSentinel } from "@/components/ui/infinite-scroll-sentinel";
import { Skeleton } from "@/components/ui/skeleton";
import { useBottomBarHeight } from "@/components/ui/toast";
import { getErrorMessage, isApiError } from "@/lib/api/errors";
import { cn } from "@/lib/cn";
import {
  invalidateDmSummaries,
  useConversation,
  useDmCharacter,
  useMarkConversationRead,
} from "@/lib/queries/dm";
import { queryKeys } from "@/lib/queries/keys";
import {
  flattenMessagesAsc,
  mergeTimeline,
  useMessages,
  useMessagesRealtime,
} from "@/lib/queries/messages";
import { ConversationIntro } from "./conversation-intro";
import { DmHeader } from "./dm-header";
import { MessageBubble } from "./message-bubble";
import { MessageComposer } from "./message-composer";
import { buildTimelineRows, holdCharacterReplies, isUuid } from "./timeline";
import { TypingIndicator } from "./typing-indicator";
import { useChatScroll } from "./use-chat-scroll";
import { useSendMessage } from "./use-send-message";

/** 入力欄の高さの初期値（safe-area を除く） */
const DEFAULT_FOOTER_PX = 56;

export interface DmConversationProps {
  characterId: string;
}

/**
 * DM 会話画面（/dm/[characterId]、仕様 §5.6 / C-2）。
 * 開いたら POST /conversations で会話を取得または作成（初回はキャラの挨拶が届く）し、
 * メッセージを表示・送信する。ヘッダー右の「i」でメモリパネル。
 */
export function DmConversation({ characterId }: DmConversationProps) {
  const valid = isUuid(characterId);
  const characterQuery = useDmCharacter(characterId, valid);
  const conversationQuery = useConversation(characterId, valid);
  const [memoryOpen, setMemoryOpen] = useState(false);

  const notFound =
    !valid ||
    (isApiError(conversationQuery.error) && conversationQuery.error.status === 404) ||
    (characterQuery.isSuccess && !characterQuery.isPlaceholderData && characterQuery.data === null);

  const character: PublicCharacter | null | undefined = notFound
    ? null
    : (characterQuery.data ?? undefined);
  const conversation = conversationQuery.data?.conversation;

  return (
    <>
      <DmHeader
        character={character}
        onOpenInfo={conversation && character ? () => setMemoryOpen(true) : undefined}
      />
      {notFound ? (
        <EmptyState
          icon={<UserIcon size={32} strokeWidth={1.6} />}
          title="アカウントが見つかりません"
          description="リンクが正しくないか、キャラクターが公開を終了した可能性があります。"
          action={
            <Link href="/dm" className={buttonClassName({ variant: "primary" })}>
              メッセージ一覧へ
            </Link>
          }
          className="flex-1 justify-center"
        />
      ) : conversationQuery.isError && !conversation ? (
        <ErrorState
          message={getErrorMessage(conversationQuery.error)}
          onRetry={() => void conversationQuery.refetch()}
          retrying={conversationQuery.isRefetching}
          className="flex-1 justify-center"
        />
      ) : !conversation ? (
        <ConversationSkeleton />
      ) : (
        <ConversationBody
          conversationId={conversation.id}
          characterId={characterId}
          character={character ?? null}
          onOpenMemory={() => setMemoryOpen(true)}
        />
      )}
      {conversation && character ? (
        <MemoryPanel
          open={memoryOpen}
          onClose={() => setMemoryOpen(false)}
          characterId={characterId}
          characterName={character.name}
        />
      ) : null}
    </>
  );
}

interface ConversationBodyProps {
  conversationId: string;
  characterId: string;
  character: PublicCharacter | null;
  onOpenMemory: () => void;
}

function ConversationBody({
  conversationId,
  characterId,
  character,
  onOpenMemory,
}: ConversationBodyProps) {
  const queryClient = useQueryClient();
  const messagesQuery = useMessages(conversationId);
  // 分割代入すると判別共用体の絞り込みで fetchNextPage が never になるため、必要な値だけ取り出す
  const data = messagesQuery.data;
  // isSuccess は再取得の失敗で false になる（data は残る）ため、表示可否は data の有無で判断する
  const hasData = data !== undefined;
  const hasNextPage = messagesQuery.hasNextPage;
  const isFetchingNextPage = messagesQuery.isFetchingNextPage;
  const isFetchNextPageError = messagesQuery.isFetchNextPageError;
  const { fetchNextPage, refetch } = messagesQuery;
  const loadOlder = useCallback(() => void fetchNextPage(), [fetchNextPage]);
  const serverAsc = useMemo(() => flattenMessagesAsc(data), [data]);
  const characterName = character?.name ?? "";

  const latestCreatedAtRef = useRef<string | null>(null);
  useEffect(() => {
    latestCreatedAtRef.current = serverAsc[serverAsc.length - 1]?.created_at ?? null;
  }, [serverAsc]);
  const getLatestCreatedAt = useCallback(() => latestCreatedAtRef.current, []);

  // ---- 既読（開いたとき・表示中にキャラの発言が届いたとき・画面に戻ったとき）
  const markRead = useMarkConversationRead(conversationId);
  const markReadIfVisible = useCallback(() => {
    if (document.visibilityState === "visible") markRead();
  }, [markRead]);
  useEffect(() => {
    if (hasData) markReadIfVisible();
  }, [hasData, markReadIfVisible]);
  useEffect(() => {
    document.addEventListener("visibilitychange", markReadIfVisible);
    return () => document.removeEventListener("visibilitychange", markReadIfVisible);
  }, [markReadIfVisible]);

  const onRealtimeInsert = useCallback(
    (message: MessageDTO) => {
      if (message.sender_type === "character") markReadIfVisible();
    },
    [markReadIfVisible],
  );
  useMessagesRealtime(conversationId, onRealtimeInsert);

  // ---- 送信
  const [memoryNotices, setMemoryNotices] = useState<ReadonlyMap<string, number>>(() => new Map());
  const onReplied = useCallback(
    (response: ChatResponse) => {
      markReadIfVisible();
      void invalidateDmSummaries(queryClient);
      if (response.memories_created.length > 0) {
        setMemoryNotices((previous) =>
          new Map(previous).set(response.character_message.id, response.memories_created.length),
        );
        void queryClient.invalidateQueries({ queryKey: queryKeys.memories(characterId) });
      }
    },
    [characterId, markReadIfVisible, queryClient],
  );
  const sender = useSendMessage({
    conversationId,
    characterId,
    getLatestCreatedAt,
    onReplied,
  });
  const { confirmLocals } = sender;

  const { items, confirmed } = useMemo(
    () => mergeTimeline(serverAsc, sender.locals, sender.keyAliases),
    [serverAsc, sender.locals, sender.keyAliases],
  );
  useEffect(() => {
    confirmLocals(confirmed);
  }, [confirmed, confirmLocals]);

  const visibleItems = useMemo(
    () => (sender.pending ? holdCharacterReplies(items, sender.pending.holdAfter) : items),
    [items, sender.pending],
  );
  const rows = useMemo(() => buildTimelineRows(visibleItems), [visibleItems]);
  const typing = Boolean(sender.pending?.typing);
  // 返答待ちの間と、履歴の初回取得が終わるまで（読み込み中・読み込み失敗）は送信できない。
  // 履歴が無いうちに送ると、送った発言と返答を差し込むキャッシュが無く、表示から消えてしまう
  const sendDisabled = sender.pending !== null || !hasData;

  // ---- 入力欄の高さ（本文の下余白・トースト位置）
  const [footerHeight, setFooterHeight] = useState(DEFAULT_FOOTER_PX);
  useBottomBarHeight(footerHeight);

  // ---- スクロール
  const newest = visibleItems[visibleItems.length - 1];
  // 自分の最後の発言が末尾にあり保存済みなら「既読」（キャラは届いた瞬間に読む）
  const seenKey =
    newest && newest.senderType === "user" && newest.status === "sent" ? newest.key : null;
  const scroll = useChatScroll({
    ready: hasData,
    newestKey: newest?.key ?? null,
    newestIsOwn: newest?.senderType === "user",
    typing,
    bottomInset: footerHeight,
    // 送信失敗の表示・「既読」・メモリ通知で末尾の高さが変わったときも最下部を保つ
    tailSignature: `${newest?.status ?? ""}|${seenKey ?? ""}|${memoryNotices.size}`,
  });

  return (
    <>
      <div
        className="flex flex-1 flex-col justify-end"
        style={{ paddingBottom: `calc(${footerHeight}px + env(safe-area-inset-bottom) + 8px)` }}
      >
        {messagesQuery.isError && !hasData ? (
          <ErrorState
            message={getErrorMessage(messagesQuery.error)}
            onRetry={() => void refetch()}
            retrying={messagesQuery.isRefetching}
            className="my-auto"
          />
        ) : !hasData ? (
          <MessagesSkeleton />
        ) : (
          <div role="log" aria-label={`${characterName}とのメッセージ`}>
            {hasNextPage ? (
              isFetchNextPageError ? (
                <ErrorState
                  compact
                  message="過去のメッセージを読み込めませんでした"
                  onRetry={loadOlder}
                />
              ) : (
                <InfiniteScrollSentinel
                  onLoadMore={loadOlder}
                  hasMore={Boolean(hasNextPage)}
                  loading={isFetchingNextPage}
                  disabled={!scroll.initialScrollDone}
                  rootMargin="400px 0px 0px 0px"
                />
              )
            ) : (
              <ConversationIntro character={character} />
            )}

            {rows.map((row) =>
              row.kind === "separator" ? (
                <div
                  key={row.key}
                  data-row-key={row.key}
                  className="pt-4 pb-2 text-center text-[12px] leading-4 font-semibold text-ig-secondary"
                >
                  {row.label}
                </div>
              ) : (
                <div key={row.key} data-row-key={row.key}>
                  <MessageBubble
                    message={row.message}
                    isFirstInGroup={row.isFirstInGroup}
                    isLastInGroup={row.isLastInGroup}
                    characterAvatarUrl={character?.avatar_url}
                    characterName={characterName}
                    onRetry={sender.retry}
                    retryDisabled={sendDisabled}
                  />
                  {row.key === seenKey ? (
                    <p className="mt-1 pr-4 text-right text-[12px] leading-4 text-ig-secondary">
                      既読
                    </p>
                  ) : null}
                  {memoryNotices.has(row.message.id) ? (
                    <MemoryNotice name={characterName} onOpen={onOpenMemory} />
                  ) : null}
                </div>
              ),
            )}

            {typing ? (
              <TypingIndicator avatarUrl={character?.avatar_url} name={characterName} />
            ) : null}
          </div>
        )}
      </div>

      {scroll.hasUnseenNew ? (
        <div
          className="pointer-events-none fixed inset-x-0 z-20 mx-auto flex max-w-[480px] justify-center"
          style={{ bottom: `calc(${footerHeight}px + env(safe-area-inset-bottom) + 10px)` }}
        >
          <button
            type="button"
            onClick={() => scroll.scrollToBottom("smooth")}
            className="pointer-events-auto flex h-9 animate-fade-in items-center gap-1 rounded-full bg-ig-sheet px-4 text-[14px] font-semibold text-ig-blue shadow-[0_2px_12px_rgb(0_0_0/0.18)]"
          >
            新しいメッセージ
            <ChevronDownIcon size={16} strokeWidth={2.4} />
          </button>
        </div>
      ) : null}

      <MessageComposer
        onSend={sender.send}
        sendDisabled={sendDisabled}
        onHeightChange={setFooterHeight}
      />
    </>
  );
}

/** 「〇〇があなたのことを覚えました」（返答で新しい記憶が作られたとき。タップでメモリパネル） */
function MemoryNotice({ name, onOpen }: { name: string; onOpen: () => void }) {
  return (
    <div className="flex animate-fade-in justify-center px-6 pt-2 pb-1">
      <button
        type="button"
        onClick={onOpen}
        className="flex items-center gap-1 text-[12px] leading-4 text-ig-secondary pressable"
      >
        <BookmarkIcon size={13} strokeWidth={2.2} />
        <span>{name}があなたのことを覚えました</span>
        <span className="font-semibold text-ig-blue">・見る</span>
      </button>
    </div>
  );
}

function MessagesSkeleton() {
  const widths = ["w-40", "w-56", "w-32", "w-48", "w-28", "w-52"];
  return (
    <div className="space-y-3 px-3 pb-2" aria-busy="true" aria-label="読み込み中">
      {widths.map((width, index) => {
        const own = index % 3 === 1;
        return (
          <div key={width} className={cn("flex items-end gap-2", own && "justify-end")}>
            {own ? null : <Skeleton shape="circle" className="size-7" />}
            <Skeleton className={cn("h-9 rounded-[22px]", width)} />
          </div>
        );
      })}
    </div>
  );
}

function ConversationSkeleton() {
  return (
    <div className="flex flex-1 flex-col justify-end pb-[72px]">
      <div className="flex flex-col items-center gap-2 pt-8 pb-6" aria-hidden="true">
        <Skeleton shape="circle" className="size-24" />
        <Skeleton shape="text" className="mt-2 h-4 w-24" />
        <Skeleton shape="text" className="w-32" />
      </div>
      <MessagesSkeleton />
    </div>
  );
}
