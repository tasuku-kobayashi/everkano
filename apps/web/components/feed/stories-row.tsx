"use client";

import Link from "next/link";
import { useCallback, useMemo, useState } from "react";
import { AI_BADGE_LABEL, AiBadge } from "@/components/ui/ai-badge";
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

  // フィード本体の表示を優先（エラーはコンソールに出ている）。再取得の失敗（data は残る）では表示し続ける
  if (isError && !data) return null;

  return (
    <nav aria-label="ストーリーズ" className="border-b border-ig-separator">
      <ul
        className="scrollbar-none flex gap-2.5 overflow-x-auto px-2.5 pt-2.5 pb-2"
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
      className="flex w-[76px] flex-col items-center gap-1.5 pressable"
      aria-label={`${item.character.name}（${AI_BADGE_LABEL}）の${item.latestPost ? "最新の投稿" : "プロフィール"}`}
      data-testid="story"
    >
      {/* Instagram の「LIVE」と同じく、アバターの下端に小さな「AI」を重ねる（E3） */}
      <span className="relative inline-flex">
        <Avatar
          src={item.character.avatar_url}
          alt={item.character.name}
          size={62}
          ring={active ? "story" : "seen"}
        />
        <AiBadge
          variant="compact"
          className="absolute -bottom-1 left-1/2 -translate-x-1/2 ring-2 ring-ig-bg"
        />
      </span>
      <span
        className={
          active
            ? "w-full truncate text-center text-[12px] leading-4"
            : "w-full truncate text-center text-[12px] leading-4 text-ig-secondary"
        }
      >
        {item.character.handle}
      </span>
    </Link>
  );
}

/**
 * 読み込み中の 1 件。読み込み後の StoryBubble と同じ寸法にする（幅 76px、アバター 73px = 62px + リング・隙間 各 5.5px、
 * 間隔 6px、ハンドル 1 行 = 16px）。高さが違うと、読み込みが終わった時点でその下のフィード全体がずれる
 * （スクロール中ならブラウザのスクロールアンカリングで scrollY も変わり、読んでいた位置の復元がずれる）。
 * ハンドルの行は Skeleton の高さ（h-3）を上書きせず、16px の行の中に置く（cn は同じ種類のクラスの衝突を解決しない）。
 */
function StorySkeleton() {
  return (
    <li className="flex w-[76px] shrink-0 flex-col items-center gap-1.5" aria-hidden="true">
      <Skeleton shape="circle" className="size-[73px]" />
      <span className="flex h-4 w-full items-center justify-center">
        <Skeleton shape="text" className="w-12" />
      </span>
    </li>
  );
}
