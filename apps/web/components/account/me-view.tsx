"use client";

import { useMutation, useQueryClient } from "@tanstack/react-query";
import { useState, type ReactNode } from "react";
import { AppHeader } from "@/components/ui/app-header";
import { BottomSheet } from "@/components/ui/bottom-sheet";
import { Button } from "@/components/ui/button";
import { ErrorState } from "@/components/ui/error-state";
import { ChevronRightIcon } from "@/components/ui/icons";
import { Modal } from "@/components/ui/modal";
import { Skeleton } from "@/components/ui/skeleton";
import { useToast } from "@/components/ui/toast";
import {
  accountDisplayName,
  signOutAndRedirect,
  useMyAccount,
  type MyAccount,
} from "@/lib/auth/account";
import { cn } from "@/lib/cn";
import { queryKeys } from "@/lib/queries/keys";
import { getSupabaseBrowserClient } from "@/lib/supabase/client";

const DISPLAY_NAME_MAX = 30;
const APP_VERSION = "0.1.0";

export function MeView() {
  const { data: account, isPending, isError, refetch, isRefetching } = useMyAccount();

  return (
    <>
      <AppHeader
        variant="title"
        title={account ? accountDisplayName(account) : isPending ? "" : "プロフィール"}
      />
      {isPending ? (
        <MeSkeleton />
      ) : isError || !account ? (
        <ErrorState onRetry={() => void refetch()} retrying={isRefetching} />
      ) : (
        <MeContent account={account} />
      )}
    </>
  );
}

function MeContent({ account }: { account: MyAccount }) {
  const toast = useToast();
  const queryClient = useQueryClient();
  const [editOpen, setEditOpen] = useState(false);
  const [withdrawOpen, setWithdrawOpen] = useState(false);
  const [loggingOut, setLoggingOut] = useState(false);
  const displayName = accountDisplayName(account);

  const withdraw = useMutation({
    mutationFn: async () => {
      const supabase = getSupabaseBrowserClient();
      const { error } = await supabase
        .from("profiles")
        .update({ deleted_at: new Date().toISOString() })
        .eq("id", account.userId);
      if (error) throw error;
    },
    onSuccess: async () => {
      // 退会したら全端末のセッションを無効化してログイン画面へ
      const { error } = await signOutAndRedirect({ reason: "withdrawn", scope: "global" });
      if (error) {
        // サーバー側のセッション無効化に失敗しても、この端末からはログアウトさせる
        await signOutAndRedirect({ reason: "withdrawn", scope: "local" });
      }
    },
    onError: (error) => {
      console.error("[me] withdraw failed:", error);
      toast.error("退会できませんでした。通信状況を確認して再度お試しください");
    },
  });

  async function logout() {
    setLoggingOut(true);
    queryClient.clear();
    const { error } = await signOutAndRedirect();
    if (error) {
      setLoggingOut(false);
      toast.error("ログアウトできませんでした。通信状況を確認して再度お試しください");
    }
  }

  return (
    <div className="flex flex-col">
      <section className="flex items-center gap-6 px-4 pt-3 pb-4">
        <span className="flex size-[86px] shrink-0 items-center justify-center rounded-full brand-gradient text-[36px] font-bold text-white">
          {displayName.charAt(0).toUpperCase()}
        </span>
        <div className="min-w-0 flex-1">
          <p className="truncate text-[16px] leading-5 font-semibold">{displayName}</p>
          <p className="mt-1 truncate text-[14px] leading-[18px] text-ig-secondary">
            {account.email ?? "メールアドレス未設定"}
          </p>
        </div>
      </section>

      <div className="px-4 pb-6">
        <Button variant="secondary" fullWidth onClick={() => setEditOpen(true)}>
          プロフィールを編集
        </Button>
      </div>

      <SectionTitle>アカウント</SectionTitle>
      <ul className="border-y border-ig-separator">
        <Row label="メールアドレス" value={account.email ?? "—"} />
        <Row label="表示名" value={displayName} onClick={() => setEditOpen(true)} />
      </ul>

      <SectionTitle>ログイン</SectionTitle>
      <ul className="border-y border-ig-separator">
        <li>
          <button
            type="button"
            onClick={() => void logout()}
            disabled={loggingOut}
            className="flex min-h-12 w-full items-center px-4 text-left text-[15px] text-ig-blue enabled:active:bg-ig-elevated disabled:opacity-50"
          >
            {loggingOut ? "ログアウト中…" : "ログアウト"}
          </button>
        </li>
        <li className="border-t border-ig-separator">
          <button
            type="button"
            onClick={() => setWithdrawOpen(true)}
            className="flex min-h-12 w-full items-center px-4 text-left text-[15px] text-ig-red active:bg-ig-elevated"
          >
            退会する
          </button>
        </li>
      </ul>

      <p className="px-4 pt-6 pb-8 text-center text-[12px] text-ig-secondary">
        everkano v{APP_VERSION}
      </p>

      <EditDisplayNameSheet open={editOpen} onClose={() => setEditOpen(false)} account={account} />

      <Modal
        open={withdrawOpen}
        onClose={() => setWithdrawOpen(false)}
        dismissible={!withdraw.isPending}
        title="退会しますか？"
        description="退会すると、このアカウントではログインできなくなります。キャラクターとのDMや、キャラクターが覚えていることも見られなくなります。この操作は取り消せません。"
        actions={[
          {
            label: "退会する",
            variant: "destructive",
            loading: withdraw.isPending,
            onClick: () => withdraw.mutate(),
          },
          {
            label: "キャンセル",
            disabled: withdraw.isPending,
            onClick: () => setWithdrawOpen(false),
          },
        ]}
      />
    </div>
  );
}

function SectionTitle({ children }: { children: ReactNode }) {
  return (
    <h2 className="px-4 pt-4 pb-2 text-[14px] leading-[18px] font-semibold text-ig-secondary">
      {children}
    </h2>
  );
}

function Row({ label, value, onClick }: { label: string; value: string; onClick?: () => void }) {
  const content = (
    <>
      <span className="shrink-0 text-[15px]">{label}</span>
      <span className="ml-auto min-w-0 truncate pl-4 text-[15px] text-ig-secondary">{value}</span>
      {onClick ? <ChevronRightIcon size={18} className="shrink-0 text-ig-secondary" /> : null}
    </>
  );
  return (
    <li className="border-t border-ig-separator first:border-t-0">
      {onClick ? (
        <button
          type="button"
          onClick={onClick}
          className="flex min-h-12 w-full items-center gap-2 px-4 text-left active:bg-ig-elevated"
        >
          {content}
        </button>
      ) : (
        <div className="flex min-h-12 items-center gap-2 px-4">{content}</div>
      )}
    </li>
  );
}

function EditDisplayNameSheet({
  open,
  onClose,
  account,
}: {
  open: boolean;
  onClose: () => void;
  account: MyAccount;
}) {
  const toast = useToast();
  const queryClient = useQueryClient();
  const [value, setValue] = useState(account.displayName ?? "");
  const trimmed = value.trim();
  const invalid = trimmed.length === 0 || trimmed.length > DISPLAY_NAME_MAX;

  const save = useMutation({
    mutationFn: async (name: string) => {
      const { error } = await getSupabaseBrowserClient()
        .from("profiles")
        .update({ display_name: name })
        .eq("id", account.userId);
      if (error) throw error;
      return name;
    },
    onSuccess: (name) => {
      queryClient.setQueryData<MyAccount | null>(queryKeys.profile(), (prev) =>
        prev ? { ...prev, displayName: name } : prev,
      );
      toast.show("表示名を変更しました");
      onClose();
    },
    onError: (error) => {
      console.error("[me] update display_name failed:", error);
      toast.error("保存できませんでした。通信状況を確認して再度お試しください");
    },
  });

  return (
    <BottomSheet open={open} onClose={onClose} title="表示名を変更">
      <form
        className="px-4 pt-4"
        onSubmit={(event) => {
          event.preventDefault();
          if (!invalid) save.mutate(trimmed);
        }}
      >
        <label htmlFor="display-name" className="text-[12px] text-ig-secondary">
          表示名（コメント欄で自分にだけ表示されます）
        </label>
        <input
          id="display-name"
          value={value}
          onChange={(event) => setValue(event.target.value)}
          maxLength={DISPLAY_NAME_MAX + 10}
          autoComplete="nickname"
          className="mt-2 h-12 w-full rounded-xl border border-ig-input-border bg-ig-input-bg px-4 text-[16px] outline-none focus:border-ig-secondary"
        />
        <p
          className={cn(
            "mt-1 text-right text-[12px]",
            trimmed.length > DISPLAY_NAME_MAX ? "text-ig-red" : "text-ig-secondary",
          )}
        >
          {trimmed.length}/{DISPLAY_NAME_MAX}
        </p>
        <Button
          type="submit"
          fullWidth
          size="lg"
          className="mt-3"
          disabled={invalid}
          loading={save.isPending}
        >
          保存
        </Button>
      </form>
    </BottomSheet>
  );
}

function MeSkeleton() {
  return (
    <div aria-hidden="true">
      <div className="flex items-center gap-6 px-4 pt-3 pb-4">
        <Skeleton shape="circle" className="size-[86px]" />
        <div className="flex-1 space-y-2">
          <Skeleton shape="text" className="w-32" />
          <Skeleton shape="text" className="w-48" />
        </div>
      </div>
      <div className="px-4">
        <Skeleton className="h-9 w-full rounded-lg" />
      </div>
    </div>
  );
}
