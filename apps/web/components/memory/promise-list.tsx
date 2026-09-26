"use client";

import type { PromiseDTO } from "@everkano/shared";
import { useId, useMemo, useState } from "react";
import { Button } from "@/components/ui/button";
import { ClockIcon } from "@/components/ui/icons";
import { Modal } from "@/components/ui/modal";
import { useToast } from "@/components/ui/toast";
import { getErrorMessage } from "@/lib/api/errors";
import { cn } from "@/lib/cn";
import {
  formatPromiseDue,
  sortPromises,
  usePromises,
  useUpdatePromise,
} from "@/lib/queries/promises";

/**
 * メモリパネルの「約束・予定」（M6）。キャラとの約束（未達・キャラが話題にしたもの）を
 * これからの順に並べ、「完了」「取り消し」ができる。約束が無ければ何も出さない。
 */
export function PromiseList({
  characterId,
  characterName,
}: {
  characterId: string;
  characterName: string;
}) {
  const toast = useToast();
  const promisesQuery = usePromises(characterId);
  const update = useUpdatePromise(characterId);
  const [cancelling, setCancelling] = useState<PromiseDTO | null>(null);
  const headingId = useId();
  const promises = useMemo(
    () => (promisesQuery.data ? sortPromises(promisesQuery.data) : []),
    [promisesQuery.data],
  );

  const change = (promise: PromiseDTO, status: "done" | "cancelled") => {
    // 呼び出しごとの結果を受け取る（mutate の onError は最後の 1 回分しか呼ばれない）
    update.mutateAsync({ promiseId: promise.id, status }).then(
      () => toast.show(status === "done" ? "約束を完了にしました" : "約束を取り消しました"),
      (error: unknown) => toast.error(getErrorMessage(error)),
    );
  };

  if (promisesQuery.isError && !promisesQuery.data) {
    return (
      <div className="flex items-center gap-2 border-t border-ig-sheet-separator px-4 py-2.5">
        <p className="min-w-0 flex-1 text-[13px] text-ig-secondary">
          約束・予定を読み込めませんでした
        </p>
        <Button
          variant="ghost"
          size="sm"
          onClick={() => void promisesQuery.refetch()}
          loading={promisesQuery.isRefetching}
        >
          再読み込み
        </Button>
      </div>
    );
  }
  if (promises.length === 0) return null;

  return (
    <section
      aria-labelledby={headingId}
      className="border-t border-ig-sheet-separator px-4 pt-3"
      data-testid="promise-list"
    >
      <h3 id={headingId} className="text-[13px] leading-4 font-semibold text-ig-secondary">
        約束・予定
      </h3>
      <ul className="mt-1">
        {promises.map((promise) => (
          <PromiseRow
            key={promise.id}
            promise={promise}
            characterName={characterName}
            onDone={() => change(promise, "done")}
            onCancel={() => setCancelling(promise)}
          />
        ))}
      </ul>
      <Modal
        open={cancelling !== null}
        onClose={() => setCancelling(null)}
        title="この約束を取り消しますか？"
        description={`取り消すと、${characterName}はこの約束を話題にしなくなります。`}
        actions={[
          {
            label: "取り消す",
            variant: "destructive",
            onClick: () => {
              const target = cancelling;
              setCancelling(null);
              if (target) change(target, "cancelled");
            },
          },
          { label: "キャンセル", onClick: () => setCancelling(null) },
        ]}
      />
    </section>
  );
}

function PromiseRow({
  promise,
  characterName,
  onDone,
  onCancel,
}: {
  promise: PromiseDTO;
  characterName: string;
  onDone: () => void;
  onCancel: () => void;
}) {
  const due = formatPromiseDue(promise);
  return (
    <li className="border-b border-ig-sheet-separator py-2.5 last:border-b-0" data-testid="promise">
      <p className="flex flex-wrap items-center gap-x-1.5 gap-y-1 text-[12px] leading-4">
        <ClockIcon size={13} strokeWidth={2.2} className="shrink-0 text-ig-secondary" />
        <span className={cn("font-semibold", due.overdue ? "text-ig-secondary" : "text-ig-text")}>
          {due.overdue ? `過ぎた予定・${due.label}` : due.label}
        </span>
        {due.date ? <span className="text-ig-secondary">{due.date}</span> : null}
        {promise.status === "mentioned" ? (
          <span className="text-ig-secondary">・{characterName}が話題にしました</span>
        ) : null}
      </p>
      <p className="mt-1 text-[15px] leading-5 text-wrap-anywhere whitespace-pre-wrap">
        {promise.content}
      </p>
      <div className="mt-2 flex items-center gap-2">
        <Button
          variant="secondary"
          size="sm"
          onClick={onDone}
          aria-label={`完了にする: ${promise.content}`}
        >
          完了
        </Button>
        <button
          type="button"
          onClick={onCancel}
          aria-label={`取り消す: ${promise.content}`}
          className="h-8 px-3 text-[14px] font-semibold text-ig-secondary pressable"
        >
          取り消し
        </button>
      </div>
    </li>
  );
}
