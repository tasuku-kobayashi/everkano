"use client";

import { PUBLIC_CHARACTER_COLUMNS } from "@everkano/shared";
import { useState, type ReactNode } from "react";
import { SafetyResourceCard } from "@/components/dm/safety-resource-card";
import { AiBadge } from "@/components/ui/ai-badge";
import { AppHeader, HeaderIconButton } from "@/components/ui/app-header";
import { Avatar } from "@/components/ui/avatar";
import { BottomSheet } from "@/components/ui/bottom-sheet";
import { Button } from "@/components/ui/button";
import { EmptyState } from "@/components/ui/empty-state";
import { ErrorState } from "@/components/ui/error-state";
import * as Icons from "@/components/ui/icons";
import { CdnImage } from "@/components/ui/image";
import { Modal } from "@/components/ui/modal";
import { PostCardSkeleton, ListRowSkeleton } from "@/components/ui/skeleton";
import { Spinner } from "@/components/ui/spinner";
import { Switch } from "@/components/ui/switch";
import { useToast } from "@/components/ui/toast";
import { api, getErrorMessage, isApiError } from "@/lib/api";
import { formatCount, formatRelativeTime } from "@/lib/format";
import { getSupabaseBrowserClient } from "@/lib/supabase/client";

// icons.tsx のエクスポートはすべてアイコンコンポーネント
const ICONS = Object.entries(Icons) as Array<[string, (props: Icons.IconProps) => ReactNode]>;

export function UiCatalog() {
  const toast = useToast();
  const [sheet, setSheet] = useState(false);
  const [modal, setModal] = useState(false);
  const [liked, setLiked] = useState(false);
  const [on, setOn] = useState(true);

  return (
    <>
      <AppHeader
        variant="back"
        title="UI カタログ"
        subtitle="開発用"
        bordered
        right={
          <HeaderIconButton label="情報">
            <Icons.InfoIcon />
          </HeaderIconButton>
        }
      />
      <Section title="Icons">
        <div className="grid grid-cols-6 gap-3">
          {ICONS.map(([name, Icon]) => (
            <div key={name} className="flex flex-col items-center gap-1">
              <Icon />
              <span className="w-full truncate text-center text-[9px] text-ig-secondary">
                {name.replace("Icon", "")}
              </span>
            </div>
          ))}
        </div>
        <button
          type="button"
          className="mt-3 flex items-center gap-2"
          onClick={() => setLiked((v) => !v)}
        >
          {liked ? (
            <Icons.HeartFilledIcon className="animate-like-pop text-ig-red" />
          ) : (
            <Icons.HeartIcon />
          )}
          <span className="text-[14px] font-semibold">いいね！{formatCount(12345)}件</span>
        </button>
      </Section>
      <Section title="Avatar">
        <div className="flex items-end gap-3">
          <Avatar alt="美咲" size="xs" />
          <Avatar alt="美咲" size="sm" />
          <Avatar alt="ひなた" size="md" ring="seen" />
          <Avatar alt="こはる" size="lg" ring="story" />
          <Avatar alt="しずく" src="https://invalid.example/x.png" size="xl" ring="story" />
          <Avatar alt="" size="md" ring="active" />
        </div>
      </Section>
      <Section title="AI バッジ（E3）/ スイッチ">
        <div className="flex flex-wrap items-center gap-3">
          <span className="flex items-center gap-1.5 text-[14px] font-semibold">
            misaki.daily <AiBadge />
          </span>
          <span className="relative inline-flex">
            <Avatar alt="美咲" size={62} ring="story" />
            <AiBadge
              variant="compact"
              className="absolute -bottom-1 left-1/2 -translate-x-1/2 ring-2 ring-ig-bg"
            />
          </span>
          <Switch checked={on} onChange={setOn} label="キャラからメッセージを受け取る" />
        </div>
      </Section>
      <Section title="相談窓口のカード（E6）">
        <SafetyResourceCard
          resources={[
            {
              name: "よりそいホットライン",
              phone: "0120-279-338",
              hours: "24時間",
              url: null,
            },
            {
              name: "厚生労働省「まもろうよ こころ」",
              phone: null,
              hours: null,
              url: "https://www.mhlw.go.jp/mamorouyokokoro/",
            },
          ]}
        />
        <SafetyResourceCard resources={undefined} loading />
        <SafetyResourceCard resources={undefined} error onRetry={() => undefined} />
      </Section>
      <Section title="Buttons">
        <div className="flex flex-wrap gap-2">
          <Button>DMする</Button>
          <Button variant="secondary">フォロー中</Button>
          <Button variant="ghost">すべて見る</Button>
          <Button variant="danger">削除</Button>
          <Button loading>送信</Button>
          <Button disabled>無効</Button>
        </div>
        <Button fullWidth size="lg" className="mt-2">
          ログインリンクを送信
        </Button>
      </Section>
      <Section title="Overlays / Toast">
        <div className="flex flex-wrap gap-2">
          <Button variant="secondary" onClick={() => setSheet(true)}>
            シート
          </Button>
          <Button variant="secondary" onClick={() => setModal(true)}>
            モーダル
          </Button>
          <Button variant="secondary" onClick={() => toast.show("課金機能は現在準備中です")}>
            トースト
          </Button>
          <Button variant="secondary" onClick={() => toast.error("通信できませんでした")}>
            エラー
          </Button>
        </div>
      </Section>
      <Section title="CdnImage（読み込み失敗 / 有料ぼかし）">
        <div className="grid grid-cols-3 gap-0.5">
          <CdnImage src="https://invalid.example/a.jpg" alt="失敗" className="aspect-square" />
          <CdnImage src="/icons/icon-512.png" alt="アイコン" className="aspect-square" />
          <div className="relative">
            <CdnImage src="/icons/icon-512.png" alt="有料" blurred className="aspect-square" />
            <Icons.LockFilledIcon className="absolute top-1/2 left-1/2 -translate-1/2 text-white" />
          </div>
        </div>
      </Section>
      <Section title="Spinner / Skeleton">
        <Spinner size={28} className="text-ig-secondary" />
        <ListRowSkeleton />
        <PostCardSkeleton />
      </Section>
      <Section title="Empty / Error">
        <EmptyState
          icon={<Icons.PaperPlaneIcon size={30} strokeWidth={1.6} />}
          title="メッセージはまだありません"
          description="気になるキャラのプロフィールから「DMする」を押してみよう。"
        />
        <ErrorState onRetry={() => toast.show("再読み込み")} />
        <p className="px-4 text-[12px] text-ig-secondary">
          {formatRelativeTime(new Date(Date.now() - 5 * 60_000))} /{" "}
          {formatRelativeTime("2026-09-03T01:00:00Z")}
        </p>
      </Section>
      <Section title="API 疎通確認（Python API）">
        <ApiCheck />
      </Section>
      <BottomSheet open={sheet} onClose={() => setSheet(false)} title="メモリ">
        <ul>
          {["来週大阪に出張する", "猫を飼っている", "コーヒーはブラック派"].map((text) => (
            <li key={text} className="flex items-center gap-3 px-4 py-3">
              <span className="flex-1 text-[14px]">{text}</span>
              <Icons.TrashIcon size={20} className="text-ig-secondary" />
            </li>
          ))}
        </ul>
      </BottomSheet>
      <Modal
        open={modal}
        onClose={() => setModal(false)}
        icon={
          <div className="flex size-16 items-center justify-center rounded-full border-2 border-ig-text">
            <Icons.LockIcon size={30} strokeWidth={1.6} />
          </div>
        }
        title="この投稿は有料コンテンツです"
        description="120 tokens"
        actions={[
          {
            label: "購入する（準備中）",
            variant: "primary",
            onClick: () => {
              setModal(false);
              toast.show("課金機能は現在準備中です");
            },
          },
          { label: "キャンセル", onClick: () => setModal(false) },
        ]}
      />
    </>
  );
}

/** Python API への疎通確認（CORS・認証ヘッダ・エラー処理の確認用） */
function ApiCheck() {
  const [lines, setLines] = useState<string[]>([]);
  const [running, setRunning] = useState(false);

  async function run() {
    setRunning(true);
    const out: string[] = [];
    const step = async (label: string, fn: () => Promise<unknown>) => {
      try {
        const result = await fn();
        out.push(`OK   ${label}: ${JSON.stringify(result)?.slice(0, 120) ?? "(empty)"}`);
        return result;
      } catch (error) {
        const detail = isApiError(error) ? `${error.status} ${error.code}` : "";
        out.push(`FAIL ${label}: ${detail} ${getErrorMessage(error)}`);
        return undefined;
      } finally {
        setLines([...out]);
      }
    };
    await step("health", () => api.health());
    const { data: character } = await getSupabaseBrowserClient()
      .from("characters")
      .select(PUBLIC_CHARACTER_COLUMNS)
      .order("handle")
      .limit(1)
      .maybeSingle();
    if (character) {
      await step("createConversation", () =>
        api.createConversation({ character_id: character.id }),
      );
      await step("listMemories", () => api.listMemories(character.id));
      const memory = (await step("createMemory", () =>
        api.createMemory({
          character_id: character.id,
          content: "疎通確認のメモ",
          tags: ["secret"],
        }),
      )) as { id: string } | undefined;
      if (memory) await step("deleteMemory", () => api.deleteMemory(memory.id));
      await step("validation error", () =>
        api.createMemory({ character_id: character.id, content: "" }),
      );
    } else {
      out.push("characters が見つかりません（シード未投入？）");
    }
    setLines([...out]);
    setRunning(false);
  }

  return (
    <div>
      <Button variant="secondary" loading={running} onClick={() => void run()}>
        疎通確認を実行
      </Button>
      <pre
        data-testid="api-check-result"
        className="mt-3 overflow-x-auto text-[11px] leading-4 whitespace-pre-wrap text-ig-secondary"
      >
        {lines.join("\n")}
      </pre>
    </div>
  );
}

function Section({ title, children }: { title: string; children: ReactNode }) {
  return (
    <section className="border-b border-ig-separator px-4 py-4">
      <h2 className="mb-3 text-[12px] font-semibold tracking-wide text-ig-secondary uppercase">
        {title}
      </h2>
      {children}
    </section>
  );
}
