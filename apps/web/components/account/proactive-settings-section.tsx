"use client";

import { useId } from "react";
import { ErrorState } from "@/components/ui/error-state";
import { Skeleton } from "@/components/ui/skeleton";
import { Switch } from "@/components/ui/switch";
import { getErrorMessage } from "@/lib/api/errors";
import { cn } from "@/lib/cn";
import {
  describeQuietHours,
  formatHour,
  QUIET_HOURS,
  useProactiveSettings,
  useUpdateProactiveGlobal,
} from "@/lib/queries/proactive";

/** 設定の説明（JSX の改行で文の間に空白が入らないよう 1 つの文字列にする） */
const PROACTIVE_DESCRIPTION =
  "キャラクターが自分からメッセージを送ってくることがあります（1日に届く数には上限があります）。" +
  "「送らない時間帯」（日本時間）には届きません。開始と終了を同じ時刻にすると、時間帯の制限はなくなります。" +
  "キャラごとのオン・オフは、DM画面右上の「i」から変更できます。";

/**
 * /me の「キャラからのメッセージ」（E4 / P3）。
 * - 全体のオン・オフ（オフならどのキャラからも自発メッセージは届かない）
 * - 送らない時間帯（日本時間の開始・終了。既定 0:00〜7:00。開始と終了を同じにすると制限なし）
 * キャラ別のオン・オフは DM の「i」から。変更はすぐに保存する（失敗したら元に戻してトースト）。
 */
export function ProactiveSettingsSection() {
  const settingsQuery = useProactiveSettings();
  const update = useUpdateProactiveGlobal();
  const headingId = useId();
  const toggleLabelId = useId();
  const descriptionId = useId();
  const startId = useId();
  const endId = useId();
  const settings = settingsQuery.data?.global;

  return (
    <section aria-labelledby={headingId} data-testid="proactive-settings">
      <h2
        id={headingId}
        className="px-4 pt-4 pb-2 text-[14px] leading-[18px] font-semibold text-ig-secondary"
      >
        キャラからのメッセージ
      </h2>
      {settings ? (
        <ul className="border-y border-ig-separator">
          <li className="flex min-h-12 items-center gap-2 py-1 pr-2 pl-4">
            <span id={toggleLabelId} className="min-w-0 flex-1 text-[15px] leading-5">
              キャラからメッセージを受け取る
            </span>
            <Switch
              checked={settings.enabled}
              onChange={(enabled) => update.mutate({ enabled })}
              labelledBy={toggleLabelId}
              describedBy={descriptionId}
              data-testid="proactive-global-switch"
            />
          </li>
          <li className="border-t border-ig-separator px-4 py-3">
            <div className="flex items-center gap-2">
              <span className="min-w-0 flex-1 text-[15px] leading-5">送らない時間帯</span>
              <span className="text-[14px] text-ig-secondary" aria-live="polite">
                {describeQuietHours(settings.quiet_start, settings.quiet_end)}
              </span>
            </div>
            <div className={cn("mt-2 flex items-center gap-2", !settings.enabled && "opacity-60")}>
              <HourSelect
                id={startId}
                label="開始"
                value={settings.quiet_start}
                onChange={(quiet_start) => update.mutate({ quiet_start })}
              />
              <span aria-hidden="true" className="text-ig-secondary">
                〜
              </span>
              <HourSelect
                id={endId}
                label="終了"
                value={settings.quiet_end}
                onChange={(quiet_end) => update.mutate({ quiet_end })}
              />
            </div>
          </li>
        </ul>
      ) : settingsQuery.isError ? (
        <ErrorState
          compact
          message={getErrorMessage(settingsQuery.error)}
          onRetry={() => void settingsQuery.refetch()}
          retrying={settingsQuery.isRefetching}
        />
      ) : (
        <div className="space-y-3 border-y border-ig-separator px-4 py-3" aria-busy="true">
          <Skeleton shape="text" className="w-48" />
          <Skeleton shape="text" className="w-36" />
        </div>
      )}
      <p id={descriptionId} className="px-4 pt-2 text-[12px] leading-4 text-ig-secondary">
        {PROACTIVE_DESCRIPTION}
      </p>
    </section>
  );
}

function HourSelect({
  id,
  label,
  value,
  onChange,
}: {
  id: string;
  label: string;
  value: number;
  onChange: (hour: number) => void;
}) {
  return (
    <label htmlFor={id} className="flex flex-1 items-center gap-2">
      <span className="shrink-0 text-[13px] text-ig-secondary">{label}</span>
      <select
        id={id}
        value={value}
        onChange={(event) => onChange(Number(event.target.value))}
        className="h-10 min-w-0 flex-1 rounded-lg border border-ig-input-border bg-ig-input-bg px-3 text-[16px] text-ig-text outline-none focus:border-ig-secondary"
      >
        {QUIET_HOURS.map((hour) => (
          <option key={hour} value={hour}>
            {formatHour(hour)}
          </option>
        ))}
      </select>
    </label>
  );
}
