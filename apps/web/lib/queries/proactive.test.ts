import type { ProactiveSettingsResponse } from "@everkano/shared";
import { describe, expect, it } from "vitest";
import { isProactiveSettingsResponse } from "@/lib/api/normalize";
import {
  applyCharacterPatch,
  applyGlobalPatch,
  DEFAULT_GLOBAL_SETTINGS,
  describeQuietHours,
  formatHour,
  inverseGlobalPatch,
  isCharacterProactiveEnabled,
  QUIET_HOURS,
} from "./proactive";

const settings: ProactiveSettingsResponse = {
  global: { enabled: true, quiet_start: 0, quiet_end: 7 },
  characters: [{ character_id: "a", enabled: false }],
};

describe("自発メッセージの設定（純粋関数）", () => {
  it("既定は受け取る・0:00〜7:00 は送らない（P3）", () => {
    expect(DEFAULT_GLOBAL_SETTINGS).toEqual({ enabled: true, quiet_start: 0, quiet_end: 7 });
    expect(QUIET_HOURS).toHaveLength(24);
    expect(QUIET_HOURS[0]).toBe(0);
    expect(QUIET_HOURS[23]).toBe(23);
  });

  it("キャラ別: 行が無いキャラはオン", () => {
    expect(isCharacterProactiveEnabled(settings, "a")).toBe(false);
    expect(isCharacterProactiveEnabled(settings, "b")).toBe(true);
    expect(isCharacterProactiveEnabled(undefined, "a")).toBe(true);
  });

  it("キャラ別の変更: 既存の行は書き換え、無ければ追加する", () => {
    expect(applyCharacterPatch(settings, "a", true).characters).toEqual([
      { character_id: "a", enabled: true },
    ]);
    expect(applyCharacterPatch(settings, "b", false).characters).toEqual([
      { character_id: "a", enabled: false },
      { character_id: "b", enabled: false },
    ]);
  });

  it("全体の変更は指定した項目だけ。失敗したら変えた項目だけ元に戻せる", () => {
    const patched = applyGlobalPatch(settings, { quiet_start: 23 });
    expect(patched.global).toEqual({ enabled: true, quiet_start: 23, quiet_end: 7 });
    const inverse = inverseGlobalPatch(settings, { quiet_start: 23 });
    expect(inverse).toEqual({ quiet_start: 0 });
    // 並行して別の項目（enabled）を変えていても、それは戻さない
    const concurrent = applyGlobalPatch(patched, { enabled: false });
    expect(applyGlobalPatch(concurrent, inverse).global).toEqual({
      enabled: false,
      quiet_start: 0,
      quiet_end: 7,
    });
  });

  it("送らない時間帯の表示（開始 = 終了は制限なし・日付をまたぐ場合は「翌」）", () => {
    expect(formatHour(7)).toBe("7:00");
    expect(describeQuietHours(0, 7)).toBe("0:00〜7:00");
    expect(describeQuietHours(23, 7)).toBe("23:00〜翌7:00");
    expect(describeQuietHours(3, 3)).toBe("時間帯の制限なし");
  });
});

describe("isProactiveSettingsResponse", () => {
  it("設定全体の形なら true（PUT の応答が設定全体でない API にも対応するため）", () => {
    expect(isProactiveSettingsResponse(settings)).toBe(true);
    expect(isProactiveSettingsResponse({ ...settings, characters: [] })).toBe(true);
    expect(isProactiveSettingsResponse(settings.global)).toBe(false);
    expect(
      isProactiveSettingsResponse({ ...settings, global: { ...settings.global, quiet_end: 24 } }),
    ).toBe(false);
    expect(isProactiveSettingsResponse(null)).toBe(false);
  });
});
