"use client";

import type { PublicCharacter } from "@everkano/shared";
import Link from "next/link";
import { useMemo, useState } from "react";
import { OPAQUE_HEADER } from "@/components/post/opaque-header";
import { PageUnavailable } from "@/components/post/page-unavailable";
import { ActionSheet } from "@/components/post/post-options-sheet";
import { PostGrid, PostGridSkeleton } from "@/components/post/post-grid";
import { useCopyLink } from "@/components/post/use-share";
import { AppHeader, HeaderIconButton } from "@/components/ui/app-header";
import { Avatar } from "@/components/ui/avatar";
import { buttonClassName } from "@/components/ui/button";
import { EmptyState } from "@/components/ui/empty-state";
import { ErrorState } from "@/components/ui/error-state";
import { ImageIcon, LockIcon, MoreIcon } from "@/components/ui/icons";
import { InfiniteScrollSentinel } from "@/components/ui/infinite-scroll-sentinel";
import { Skeleton } from "@/components/ui/skeleton";
import { cn } from "@/lib/cn";
import { formatCount } from "@/lib/format";
import {
  normalizeHandle,
  useCharacter,
  useCharacterPostCount,
  useCharacterPosts,
} from "@/lib/queries/characters";
import { useStories } from "@/lib/queries/feed";
import type { CharacterPostsTab } from "@/lib/queries/keys";
import { ProfileTabs } from "./profile-tabs";

/** キャラクタープロフィール（/c/[handle]） */
export function CharacterProfileView({ handle: rawHandle }: { handle: string }) {
  const handle = useMemo(() => normalizeHandle(rawHandle), [rawHandle]);
  const { data: character, isPending, isError, refetch, isRefetching } = useCharacter(handle ?? "");
  const [sheetOpen, setSheetOpen] = useState(false);
  const copyLink = useCopyLink();

  const invalid = handle === null;

  return (
    <>
      <AppHeader
        variant="back"
        className={OPAQUE_HEADER}
        // 見つからない場合は Instagram と同じく見出しを出さない
        title={character?.handle ?? (invalid || character === null ? "" : handle)}
        right={
          character ? (
            <HeaderIconButton label="その他のオプション" onClick={() => setSheetOpen(true)}>
              <MoreIcon size={24} />
            </HeaderIconButton>
          ) : undefined
        }
      />
      {invalid ? (
        <PageUnavailable />
      ) : isPending ? (
        <ProfileSkeleton />
      ) : isError ? (
        <ErrorState onRetry={() => void refetch()} retrying={isRefetching} />
      ) : !character ? (
        <PageUnavailable />
      ) : (
        <ProfileContent character={character} />
      )}
      {character ? (
        <ActionSheet
          open={sheetOpen}
          onClose={() => setSheetOpen(false)}
          ariaLabel="プロフィールのオプション"
          actions={[
            {
              label: "プロフィールのリンクをコピー",
              onClick: () => {
                setSheetOpen(false);
                void copyLink(`/c/${character.handle}`);
              },
            },
          ]}
        />
      ) : null}
    </>
  );
}

function ProfileContent({ character }: { character: PublicCharacter }) {
  const [tab, setTab] = useState<CharacterPostsTab>("free");
  return (
    <>
      <ProfileHeader character={character} />
      <ProfileTabs value={tab} onChange={setTab} />
      <div role="tabpanel" id={`profile-panel-${tab}`} aria-labelledby={`profile-tab-${tab}`}>
        <ProfilePosts key={tab} characterId={character.id} tab={tab} />
      </div>
    </>
  );
}

/** プロフィール上部（Instagram: 大きなアバター + 投稿数 / フォロワー数、名前、自己紹介、ボタン） */
function ProfileHeader({ character }: { character: PublicCharacter }) {
  const { data: postCount, isPending: countPending } = useCharacterPostCount(character.id);
  const { data: stories } = useStories();
  const story = stories?.find((item) => item.character.id === character.id);
  const hasRecentPost = Boolean(story?.isRecent && story.latestPost);

  const avatar = (
    <Avatar
      src={character.avatar_url}
      alt={character.name}
      size="2xl"
      ring={hasRecentPost ? "story" : "none"}
    />
  );

  return (
    <section className="px-4 pt-3" data-testid="profile-header">
      <div className="flex items-center gap-4">
        {hasRecentPost && story?.latestPost ? (
          <Link
            href={`/posts/${story.latestPost.id}`}
            aria-label={`${character.name}の最新の投稿`}
            className="pressable shrink-0"
          >
            {avatar}
          </Link>
        ) : (
          <span className="shrink-0">{avatar}</span>
        )}
        <dl className="flex flex-1 items-center justify-around text-center">
          <Stat
            label="投稿"
            value={countPending ? null : formatCount(postCount ?? 0)}
            testId="stat-posts"
          />
          <Stat label="フォロワー" value={formatCount(character.follower_count)} />
        </dl>
      </div>
      <h2 className="mt-3 text-[14px] leading-[18px] font-semibold">{character.name}</h2>
      {character.bio ? (
        <p className="text-wrap-anywhere text-[14px] leading-[18px] whitespace-pre-line">
          {character.bio}
        </p>
      ) : null}
      <Link
        href={`/dm/${character.id}`}
        className={cn(buttonClassName({ variant: "primary", size: "sm", fullWidth: true }), "mt-4")}
        data-testid="dm-button"
      >
        DMする
      </Link>
    </section>
  );
}

function Stat({ label, value, testId }: { label: string; value: string | null; testId?: string }) {
  return (
    <div className="flex min-w-[64px] flex-col-reverse items-center" data-testid={testId}>
      <dt className="text-[13px] leading-4">{label}</dt>
      <dd className="text-[17px] leading-[22px] font-semibold">
        {value ?? <Skeleton shape="text" className="my-1 h-3.5 w-6" />}
      </dd>
    </div>
  );
}

/** 無料 / 有料タブのグリッド（無限スクロール） */
function ProfilePosts({ characterId, tab }: { characterId: string; tab: CharacterPostsTab }) {
  const {
    data,
    isPending,
    isError,
    refetch,
    isRefetching,
    fetchNextPage,
    hasNextPage,
    isFetchingNextPage,
    isFetchNextPageError,
  } = useCharacterPosts(characterId, tab);
  const posts = useMemo(() => data?.pages.flatMap((page) => page.posts) ?? [], [data]);

  if (isPending) return <PostGridSkeleton rows={2} />;
  if (isError && posts.length === 0) {
    return <ErrorState compact onRetry={() => void refetch()} retrying={isRefetching} />;
  }
  if (posts.length === 0) {
    return tab === "free" ? (
      <EmptyState icon={<ImageIcon size={30} strokeWidth={1.6} />} title="投稿はまだありません" />
    ) : (
      <EmptyState
        icon={<LockIcon size={28} strokeWidth={1.6} />}
        title="有料コンテンツはまだありません"
      />
    );
  }
  return (
    <>
      <PostGrid posts={posts} />
      <InfiniteScrollSentinel
        onLoadMore={() => void fetchNextPage()}
        hasMore={Boolean(hasNextPage)}
        loading={isFetchingNextPage}
        disabled={isFetchNextPageError}
      />
      {isFetchNextPageError ? (
        <ErrorState
          compact
          message="続きを読み込めませんでした"
          onRetry={() => void fetchNextPage()}
        />
      ) : null}
    </>
  );
}

function ProfileSkeleton() {
  return (
    <div aria-busy="true" aria-label="読み込み中">
      <div className="px-4 pt-3" aria-hidden="true">
        <div className="flex items-center gap-4">
          <Skeleton shape="circle" className="size-[86px] shrink-0" />
          <div className="flex flex-1 justify-around">
            {[0, 1].map((i) => (
              <div key={i} className="flex flex-col items-center gap-2">
                <Skeleton shape="text" className="h-4 w-8" />
                <Skeleton shape="text" className="w-14" />
              </div>
            ))}
          </div>
        </div>
        <Skeleton shape="text" className="mt-4 w-24" />
        <Skeleton shape="text" className="mt-2 w-4/5" />
        <Skeleton shape="text" className="mt-2 w-3/5" />
        <Skeleton className="mt-4 h-8 w-full rounded-lg" />
      </div>
      <div className="border-ig-separator mt-4 flex h-11 border-b" />
      <PostGridSkeleton rows={2} />
    </div>
  );
}
