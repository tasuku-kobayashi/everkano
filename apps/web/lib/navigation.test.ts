import { beforeEach, describe, expect, it, vi } from "vitest";
import type * as Navigation from "./navigation";

type NavigationModule = typeof Navigation;

/** 状態はモジュール変数なので、テストごとに読み込み直す（= ページの再読み込みと同じ） */
async function load(): Promise<NavigationModule> {
  vi.resetModules();
  return import("./navigation");
}

describe("アプリ内の戻る履歴（navigation depth）", () => {
  let nav: NavigationModule;
  beforeEach(async () => {
    nav = await load();
  });

  it("直接開いた直後は戻れる履歴が無い", () => {
    expect(nav.canGoBackInApp()).toBe(false);
  });

  it("push（Link / router.push）の遷移は数え、popstate で戻る", () => {
    nav.recordPathnameChange(); // / → /posts/1
    expect(nav.canGoBackInApp()).toBe(true);
    nav.recordPopState(); // ブラウザの戻る
    nav.recordPathnameChange(); // /posts/1 → /（popstate による画面変化は数えない）
    expect(nav.canGoBackInApp()).toBe(false);
  });

  it("router.replace（履歴が増えない遷移）は数えない", () => {
    // 例: 戻るボタンのフォールバック（履歴が無い → "/" へ replace）
    nav.markReplaceNavigation();
    nav.recordPathnameChange();
    expect(nav.canGoBackInApp()).toBe(false);
    // その後の通常の遷移は数える
    nav.recordPathnameChange();
    expect(nav.canGoBackInApp()).toBe(true);
  });

  it("ページを読み込み直すと（ログイン後のハードナビゲーション等）0 に戻る", async () => {
    nav.recordPathnameChange();
    expect(nav.canGoBackInApp()).toBe(true);
    nav = await load();
    expect(nav.canGoBackInApp()).toBe(false);
  });
});
