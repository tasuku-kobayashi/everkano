"use client";

import Link from "next/link";
import { useCallback, useMemo, useState } from "react";
import { Avatar } from "@/components/ui/avatar";
import { Skeleton } from "@/components/ui/skeleton";
import { useStories, type StoryItem } from "@/lib/queries/feed";

/** 閲覧済みのストーリー（= 最新投稿 ID）を端末に覚えておく（リングをグレーにするだけの見た目の機能） */
const SEEN_STORAGE_KEY = "everkano:seen-stories";
const SEEN_MAX = 200;

function loadSeen(): Set<string> {
  if (typeof window === "undefined") return new Set();
  try {
    const raw = window.localStorage.getItem(SEEN_STORAGE_KEY);
    const parsed: unknown = raw ? JSON.parse(raw) : [];
    return new Set(Array.isArray(parsed) ? parsed.filter((v) => typeof v === "string") : []);
  } catch {
    return new Set();
  }
}

function useSeenStories() {
  const [seen, setSeen] = useState<Set<string>>(loadSeen);
  const markSeen = useCallback((postId: string) => {
    setSeen((prev) => {
      if (prev.has(postId)) return prev;
      const next = new Set(prev);
      next.add(postId);
      try {
        window.localStorage.setItem(SEEN_STORAGE_KEY, JSON.stringify([...next].slice(-SEEN_MAX)));
      } catch (error) {
        console.warn("[stories] failed to persist seen state:", error);
      }
      return next;
    });
  }, []);
  return { seen, markSeen };
}

/**
 * ホーム上部のストーリーズ風アバター行（仕様 §4.3: キャラの「今日の投稿」へのショートカット。24 時間で消える仕様は無し）。
 * 24 時間以内に投稿したキャラはグラデーションのリング、それ以外（または閲覧済み）はグレーのリング。
 * タップでそのキャラの最新投稿へ（投稿が無ければプロフィールへ）。
 */
export function StoriesRow() {
  const { data, isPending, isError } = useStories();
  const { seen, markSeen } = useSeenStories();

  // Instagram と同じく、未読（24 時間以内に投稿 かつ 未閲覧）を先頭に。順序はそれ以外は維持する
  const items = useMemo(() => {
    if (!data) return [];
    const isActive = (item: StoryItem) =>
      item.isRecent && item.latestPost !== null && !seen.has(item.latestPost.id);
    return [...data.filter(isActive), ...data.filter((item) => !isActive(item))];
  }, [data, seen]);

  if (isError) return null; // フィード本体の表示を優先（エラーはコンソールに出ている）

  return (
    <nav aria-label="ストーリーズ" className="border-ig-separator border-b">
      <ul
        className="flex scrollbar-none gap-2.5 overflow-x-auto px-2.5 pt-2.5 pb-2"
        data-testid="stories-row"
      >
        {isPending
          ? Array.from({ length: 6 }, (_, i) => <StorySkeleton key={i} />)
          : items.map((item) => (
              <li key={item.character.id} className="shrink-0">
                <StoryBubble
                  item={item}
                  seen={item.latestPost ? seen.has(item.latestPost.id) : true}
                  onOpen={() => {
                    if (item.latestPost) markSeen(item.latestPost.id);
                  }}
                />
              </li>
            ))}
      </ul>
    </nav>
  );
}

function StoryBubble({
  item,
  seen,
  onOpen,
}: {
  item: StoryItem;
  seen: boolean;
  onOpen: () => void;
}) {
  const href = item.latestPost ? `/posts/${item.latestPost.id}` : `/c/${item.character.handle}`;
  const active = item.isRecent && !seen;
  return (
    <Link
      href={href}
      onClick={onOpen}
      className="pressable flex w-[76px] flex-col items-center gap-1"
      aria-label={`${item.character.name}の${item.latestPost ? "最新の投稿" : "プロフィール"}`}
      data-testid="story"
    >
      <Avatar
        src={item.character.avatar_url}
        alt={item.character.name}
        size={62}
        ring={active ? "story" : "seen"}
      />
      <span
        className={
          active
            ? "w-full truncate text-center text-[12px] leading-4"
            : "text-ig-secondary w-full truncate text-center text-[12px] leading-4"
        }
      >
        {item.character.handle}
      </span>
    </Link>
  );
}

function StorySkeleton() {
  return (
    <li className="flex w-[76px] shrink-0 flex-col items-center gap-1.5" aria-hidden="true">
      <Skeleton shape="circle" className="size-[73px]" />
      <Skeleton shape="text" className="h-2.5 w-12" />
    </li>
  );
}
