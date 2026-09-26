"use client";

import { useId } from "react";
import { Button } from "@/components/ui/button";
import { Switch } from "@/components/ui/switch";
import {
  isCharacterProactiveEnabled,
  useProactiveSettings,
  useUpdateProactiveCharacter,
} from "@/lib/queries/proactive";

/**
 * DM の「i」（メモリパネル）の先頭: そのキャラからの自発メッセージのオン・オフ（E4 / P3）。
 * 全体の設定（/me）がオフのときは切り替えられない（その旨を表示する）。
 */
export function ProactiveToggleRow({
  characterId,
  characterName,
}: {
  characterId: string;
  characterName: string;
}) {
  const settingsQuery = useProactiveSettings();
  const update = useUpdateProactiveCharacter(characterId);
  const labelId = useId();
  const descriptionId = useId();
  const settings = settingsQuery.data;
  const globalOff = settings ? !settings.global.enabled : false;
  const enabled = isCharacterProactiveEnabled(settings, characterId);

  const description = !settings
    ? settingsQuery.isError
      ? "設定を読み込めませんでした。"
      : "設定を読み込んでいます…"
    : globalOff
      ? "プロフィール画面の「キャラからのメッセージ」がオフのため、どのキャラからも届きません。"
      : `ときどき${characterName}から話しかけてきます。`;

  return (
    <div
      className="flex items-center gap-2 border-b border-ig-sheet-separator py-1.5 pr-2 pl-4"
      data-testid="proactive-character-setting"
    >
      <div className="min-w-0 flex-1">
        <p id={labelId} className="text-[15px] leading-5">
          {characterName}からのメッセージ
        </p>
        <p id={descriptionId} className="text-[12px] leading-4 text-ig-secondary">
          {description}
        </p>
      </div>
      {settingsQuery.isError && !settings ? (
        <Button
          variant="ghost"
          size="sm"
          onClick={() => void settingsQuery.refetch()}
          loading={settingsQuery.isRefetching}
        >
          再読み込み
        </Button>
      ) : (
        <Switch
          checked={settings ? enabled && !globalOff : false}
          disabled={!settings || globalOff}
          onChange={(next) => update.mutate(next)}
          labelledBy={labelId}
          describedBy={descriptionId}
          data-testid="proactive-character-switch"
        />
      )}
    </div>
  );
}
