import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
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

// ---------------------------------------------------------------------------
// オーバーレイ（シート・モーダル）と端末の「戻る」
// ---------------------------------------------------------------------------

interface FakeEntry {
  state: unknown;
  href: string;
}

/**
 * history / location の最小の偽物。history.go() は非同期に popstate を発火し、
 * nav.handlePopState が stopImmediatePropagation しなかったものだけを「Next.js に届いた」として記録する。
 */
function installFakeBrowser(nav: NavigationModule, href = "https://app.test/me") {
  const entries: FakeEntry[] = [{ state: { __NA: true, tree: "me" }, href }];
  let index = 0;
  const deliveredToNext: unknown[] = [];

  const firePopState = () => {
    let stopped = false;
    nav.handlePopState({
      state: entries[index]?.state ?? null,
      stopImmediatePropagation: () => {
        stopped = true;
      },
    });
    if (!stopped) deliveredToNext.push(entries[index]?.state ?? null);
  };

  const history = {
    get state() {
      return entries[index]?.state ?? null;
    },
    get length() {
      return entries.length;
    },
    pushState(state: unknown, _unused: string, url?: string) {
      entries.splice(index + 1);
      entries.push({ state, href: url ?? entries[index]!.href });
      index += 1;
    },
    go(delta: number) {
      setTimeout(() => {
        const target = Math.min(entries.length - 1, Math.max(0, index + delta));
        if (target === index) return;
        index = target;
        firePopState();
      }, 0);
    },
  };
  vi.stubGlobal("window", {
    history,
    location: {
      get href() {
        return entries[index]!.href;
      },
    },
  });

  return {
    entries,
    get index() {
      return index;
    },
    deliveredToNext,
    /** ユーザーが端末の「戻る」を押した */
    back() {
      index -= 1;
      firePopState();
    },
    /** Next.js の router.push（別の URL のエントリを積む） */
    navigate(url: string) {
      history.pushState({ __NA: true, tree: url }, "", url);
    },
  };
}

const flush = () => new Promise((resolve) => setTimeout(resolve, 5));

describe("オーバーレイと端末の「戻る」", () => {
  let nav: NavigationModule;
  beforeEach(async () => {
    nav = await load();
  });
  afterEach(() => {
    vi.unstubAllGlobals();
  });

  it("開くと同じ URL の履歴を 1 つ積み、「戻る」でオーバーレイだけを閉じる（Next.js と depth に渡さない）", async () => {
    const browser = installFakeBrowser(nav);
    nav.recordPathnameChange(); // / → /me（アプリ内で 1 回遷移済み）
    const onPop = vi.fn();
    nav.registerOverlayHistoryEntry("sheet", onPop);
    await flush();
    expect(browser.entries).toHaveLength(2);
    expect(browser.entries[1]?.href).toBe("https://app.test/me");
    // Next.js の履歴 state は引き継ぐ
    expect(browser.entries[1]?.state).toMatchObject({
      __NA: true,
      [nav.OVERLAY_HISTORY_KEY]: "sheet",
    });

    browser.back();
    expect(onPop).toHaveBeenCalledTimes(1);
    expect(browser.deliveredToNext).toEqual([]);
    expect(nav.canGoBackInApp()).toBe(true); // depth は減らない
  });

  it("UI で閉じたら積んだ履歴を取り除き、その popstate は Next.js に渡さない", async () => {
    const browser = installFakeBrowser(nav);
    nav.recordPathnameChange();
    const onPop = vi.fn();
    const release = nav.registerOverlayHistoryEntry("sheet", onPop);
    await flush();
    release();
    release(); // 冪等
    await flush();
    expect(browser.index).toBe(0);
    expect(onPop).not.toHaveBeenCalled();
    expect(browser.deliveredToNext).toEqual([]);
    expect(nav.canGoBackInApp()).toBe(true);
  });

  it("シートの上のダイアログと同時に閉じた場合は 1 回でまとめて戻す", async () => {
    const browser = installFakeBrowser(nav);
    const releaseSheet = nav.registerOverlayHistoryEntry("sheet", vi.fn());
    await flush();
    const releaseDialog = nav.registerOverlayHistoryEntry("dialog", vi.fn());
    await flush();
    expect(browser.entries).toHaveLength(3);
    // 閉じる順序（effect の実行順）に依存しない
    releaseSheet();
    releaseDialog();
    await flush();
    expect(browser.index).toBe(0);
    expect(browser.deliveredToNext).toEqual([]);
  });

  it("入れ子のとき「戻る」は一番上のダイアログだけを閉じる", async () => {
    const browser = installFakeBrowser(nav);
    const sheetPop = vi.fn();
    const dialogPop = vi.fn();
    nav.registerOverlayHistoryEntry("sheet", sheetPop);
    await flush();
    nav.registerOverlayHistoryEntry("dialog", dialogPop);
    await flush();
    browser.back();
    expect(dialogPop).toHaveBeenCalledTimes(1);
    expect(sheetPop).not.toHaveBeenCalled();
    browser.back();
    expect(sheetPop).toHaveBeenCalledTimes(1);
    expect(browser.deliveredToNext).toEqual([]);
  });

  it("シートの操作で別の画面へ遷移した後に閉じても、履歴を戻さない", async () => {
    const browser = installFakeBrowser(nav);
    const release = nav.registerOverlayHistoryEntry("sheet", vi.fn());
    await flush();
    browser.navigate("https://app.test/c/misaki_ol");
    release(); // 遷移でアンマウント
    await flush();
    expect(browser.index).toBe(2);
    expect(window.location.href).toBe("https://app.test/c/misaki_ol");
  });

  it("閉じる操作と同時に始めた遷移（router.push）は取り消されない（Next.js に復元を渡さない）", async () => {
    const browser = installFakeBrowser(nav);
    nav.recordPathnameChange();
    const release = nav.registerOverlayHistoryEntry("sheet", vi.fn());
    await flush();
    // 「プロフィールを見る」: onClose() → router.push()。push の反映（pushState）は RSC の取得後
    release();
    await flush();
    expect(browser.deliveredToNext).toEqual([]); // 復元（= 保留中の遷移の破棄）が起きない
    browser.navigate("https://app.test/c/misaki_ol");
    expect(browser.entries.map((e) => e.href)).toEqual([
      "https://app.test/me",
      "https://app.test/c/misaki_ol",
    ]);
  });

  it("StrictMode の effect 二重実行（登録 → 即解除 → 再登録）でも 1 つしか積まない", async () => {
    const browser = installFakeBrowser(nav);
    const release = nav.registerOverlayHistoryEntry("sheet", vi.fn());
    release();
    nav.registerOverlayHistoryEntry("sheet", vi.fn());
    await flush();
    expect(browser.entries).toHaveLength(2);
    expect(browser.deliveredToNext).toEqual([]);
  });

  it("オーバーレイが無いときの popstate は通常どおり depth を減らして Next.js に渡す", () => {
    const browser = installFakeBrowser(nav);
    browser.navigate("https://app.test/posts/1");
    nav.recordPathnameChange();
    expect(nav.canGoBackInApp()).toBe(true);
    browser.back();
    expect(browser.deliveredToNext).toHaveLength(1);
    expect(nav.canGoBackInApp()).toBe(false);
  });
});
