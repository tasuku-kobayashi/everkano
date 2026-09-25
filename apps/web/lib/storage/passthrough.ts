import type { StorageAdapter } from "./types";

/**
 * passthrough ドライバー: DB の値をそのまま使う（開発時のプレースホルダURL用）。
 * 変換パラメータは無視する（ぼかしは UI 側の CSS で行う）。
 */
export function createPassthroughAdapter(): StorageAdapter {
  return {
    driver: "passthrough",
    supportsTransforms: false,
    resolveImageUrl(src) {
      return src;
    },
  };
}
