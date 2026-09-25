/**
 * className を結合する（falsy は無視）。
 * 注意: tailwind-merge 相当の衝突解決はしない。コンポーネントの variant / size で調整できない
 * 見た目の上書きは、衝突しないクラスで行うこと。
 */
export type ClassValue = string | false | null | undefined | 0;

export function cn(...classes: ClassValue[]): string {
  return classes.filter(Boolean).join(" ");
}
