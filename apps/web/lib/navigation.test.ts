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
 * 戻る先は Chromium と同じく「go() を呼んだ時点の」エントリから決める（着地までに pushState されても変わらない）。
 */
function installFakeBrowser(
  nav: NavigationModule,
  href = "https://app.test/me",
  earlier: FakeEntry[] = [],
) {
  const entries: FakeEntry[] = [...earlier, { state: { __NA: true, tree: "me" }, href }];
  let index = entries.length - 1;
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
    replaceState(state: unknown, _unused: string, url?: string) {
      entries[index] = { state, href: url ?? entries[index]!.href };
    },
    go(delta: number) {
      const target = Math.min(entries.length - 1, Math.max(0, index + delta));
      setTimeout(() => {
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
    get hrefs() {
      return entries.map((entry) => entry.href);
    },
    deliveredToNext,
    /** ユーザーが端末の「戻る」を押した */
    back() {
      index -= 1;
      firePopState();
    },
    /** Next.js の router.push（別の URL のエントリを積む。Next.js の内部の state 付き） */
    navigate(url: string) {
      window.history.pushState({ __NA: true, tree: url }, "", url);
    },
    /** Next.js のルーターの再描画・遷移の完了（今のエントリを置き換える） */
    replace(url?: string) {
      window.history.replaceState({ __NA: true, tree: url ?? "same" }, "", url);
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

  it("シートを開いたまま別の画面へ遷移すると、シートのエントリを取り除いてから遷移のエントリを積む", async () => {
    const browser = installFakeBrowser(nav);
    nav.recordPathnameChange();
    const onPop = vi.fn();
    const release = nav.registerOverlayHistoryEntry("sheet", onPop);
    await flush();
    browser.navigate("https://app.test/c/misaki_ol");
    release(); // 遷移でアンマウント
    await flush();
    // 同じ URL のシートのエントリが残らない（残すと「戻る」が 1 回空振りする）
    expect(browser.hrefs).toEqual(["https://app.test/me", "https://app.test/c/misaki_ol"]);
    expect(browser.index).toBe(1);
    expect(onPop).toHaveBeenCalledTimes(1);
    expect(browser.deliveredToNext).toEqual([]);
    expect(nav.canGoBackInApp()).toBe(true);
  });

  it("UI で閉じた直後の遷移のエントリが「戻る」の着地より先に積まれても、元の画面のエントリを失わない", async () => {
    // ログイン直後: マジックリンクの確認画面 → ホーム
    const confirm = { state: null, href: "https://app.test/auth/confirm?token_hash=x" };
    const browser = installFakeBrowser(nav, "https://app.test/", [confirm]);
    const release = nav.registerOverlayHistoryEntry("sheet", vi.fn());
    await flush();
    expect(browser.hrefs).toEqual([confirm.href, "https://app.test/", "https://app.test/"]);

    // 「プロフィールを見る」: onClose()（→ history.go(-1)）の直後、着地より先に Next.js が pushState / replaceState
    release();
    await Promise.resolve(); // 閉じる処理（microtask）で history.go(-1) を呼ぶ
    browser.navigate("https://app.test/c/misaki_ol");
    browser.replace("https://app.test/c/misaki_ol"); // RSC の取得完了による置き換え
    await flush();

    expect(browser.hrefs).toEqual([
      confirm.href,
      "https://app.test/",
      "https://app.test/c/misaki_ol",
    ]);
    expect(browser.index).toBe(2);
    expect(browser.entries[2]?.state).toEqual({ __NA: true, tree: "https://app.test/c/misaki_ol" });
    expect(browser.deliveredToNext).toEqual([]);

    // 「戻る」はホームへ（確認画面ではない）
    browser.back();
    expect(window.location.href).toBe("https://app.test/");
  });

  it("閉じたオーバーレイの印は、保留していた書き込みで元の画面のエントリに写さない", async () => {
    const browser = installFakeBrowser(nav);
    const release = nav.registerOverlayHistoryEntry("sheet", vi.fn());
    await flush();
    release();
    await Promise.resolve();
    // 着地前の replaceState（ルーターの再描画）: シートのエントリの state を写した書き込み
    window.history.replaceState({ ...(window.history.state as object), extra: 1 }, "");
    await flush();
    expect(browser.index).toBe(0);
    expect(browser.entries[0]?.state).toEqual({ __NA: true, tree: "me", extra: 1 });
  });

  it("開いている間の同じ URL の置き換え（ルーターの再描画）でも印を残し、UI で閉じたら取り除ける", async () => {
    const browser = installFakeBrowser(nav);
    const release = nav.registerOverlayHistoryEntry("sheet", vi.fn());
    await flush();
    browser.replace(); // Next.js は自分の state だけで置き換える
    expect(browser.entries[1]?.state).toMatchObject({ [nav.OVERLAY_HISTORY_KEY]: "sheet" });
    release();
    await flush();
    expect(browser.index).toBe(0);
    expect(browser.deliveredToNext).toEqual([]);
  });

  it("別の URL への置き換え（router.replace）は、シートのエントリを取り除いてから元の画面のエントリを置き換える", async () => {
    const browser = installFakeBrowser(nav);
    nav.registerOverlayHistoryEntry("sheet", vi.fn());
    await flush();
    browser.replace("https://app.test/search?q=x");
    await flush();
    expect(browser.index).toBe(0);
    expect(browser.entries[0]?.href).toBe("https://app.test/search?q=x");
    expect(browser.deliveredToNext).toEqual([]);
  });

  it("自分が積んだエントリにいないときは、UI で閉じても履歴を戻さない（前の画面・ログイン画面まで戻らない）", async () => {
    const browser = installFakeBrowser(nav, "https://app.test/", [
      { state: null, href: "https://app.test/auth/confirm?token_hash=x" },
    ]);
    const release = nav.registerOverlayHistoryEntry("sheet", vi.fn());
    await flush();
    // 何らかの理由で今のエントリがシートのものでなくなった（印の無い state への置き換え = 想定外の書き込み）
    browser.entries[browser.index] = { state: { other: true }, href: "https://app.test/" };
    release();
    await flush();
    expect(browser.index).toBe(2);
    expect(window.location.href).toBe("https://app.test/");
  });

  it("閉じた直後に開いたダイアログのエントリは、閉じたシートのエントリを取り除いた後に積む", async () => {
    const browser = installFakeBrowser(nav);
    const releaseSheet = nav.registerOverlayHistoryEntry("sheet", vi.fn());
    await flush();
    releaseSheet();
    await Promise.resolve(); // history.go(-1) を呼んだ（まだ着地していない）
    const dialogPop = vi.fn();
    nav.registerOverlayHistoryEntry("dialog", dialogPop);
    await flush();
    expect(browser.index).toBe(1);
    expect(browser.entries[1]?.state).toMatchObject({ [nav.OVERLAY_HISTORY_KEY]: "dialog" });
    // 「戻る」でダイアログだけが閉じる
    browser.back();
    expect(dialogPop).toHaveBeenCalledTimes(1);
    expect(browser.deliveredToNext).toEqual([]);
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
