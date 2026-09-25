import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { ApiError } from "@/lib/api/errors";
import { isRetryableQueryError } from "@/lib/query-retry";

/** supabase.auth.getSession() / getUser() と profiles の取得を差し替える */
const getSession = vi.fn();
const getUser = vi.fn();
const signOut = vi.fn();
const maybeSingle = vi.fn();
const profileQueriedFor = vi.fn();
vi.mock("@/lib/supabase/client", () => ({
  getSupabaseBrowserClient: () => ({
    auth: { getSession, getUser, signOut },
    from: () => ({
      select: () => ({
        eq: (_column: string, userId: string) => {
          profileQueriedFor(userId);
          return { maybeSingle: () => maybeSingle(userId) };
        },
      }),
    }),
  }),
}));

const { AccountBannedError, fetchMyAccount } = await import("./account");

const SESSION = { access_token: "access-1", user: { id: "user-1" } };

function authError(status: number, code: string, name = "AuthApiError") {
  return { data: { user: null }, error: { name, status, code, message: code } };
}

function deferred<T>() {
  let resolve!: (value: T) => void;
  const promise = new Promise<T>((r) => {
    resolve = r;
  });
  return { promise, resolve };
}

describe("fetchMyAccount", () => {
  beforeEach(() => {
    getSession.mockReset();
    getUser.mockReset();
    maybeSingle.mockReset();
    profileQueriedFor.mockReset();
    getSession.mockResolvedValue({ data: { session: SESSION }, error: null });
    maybeSingle.mockImplementation(async (userId: string) => ({
      data: { id: userId, display_name: "さくら", deleted_at: null },
      error: null,
    }));
  });

  it("有効なセッションならアカウント情報を返す（getUser にはトークンを明示して渡す）", async () => {
    getUser.mockResolvedValue({
      data: { user: { id: "user-1", email: "a@example.com" } },
      error: null,
    });
    await expect(fetchMyAccount()).resolves.toMatchObject({
      userId: "user-1",
      email: "a@example.com",
      displayName: "さくら",
      deletedAt: null,
      profileExists: true,
    });
    expect(getUser).toHaveBeenCalledWith("access-1");
  });

  it("getUser（Auth への問い合わせ）と profiles の取得を同時に始める（直列の往復にしない）", async () => {
    const user = deferred<unknown>();
    getUser.mockReturnValue(user.promise);
    const pending = fetchMyAccount();
    // getUser の応答を待たずに profiles の取得が始まっている
    await vi.waitFor(() => expect(profileQueriedFor).toHaveBeenCalledWith("user-1"));
    user.resolve({ data: { user: { id: "user-1", email: "a@example.com" } }, error: null });
    await expect(pending).resolves.toMatchObject({ userId: "user-1", displayName: "さくら" });
    expect(maybeSingle).toHaveBeenCalledTimes(1);
  });

  it("端末にセッションが無ければ Auth に問い合わせずに null", async () => {
    getSession.mockResolvedValue({ data: { session: null }, error: null });
    await expect(fetchMyAccount()).resolves.toBeNull();
    expect(getUser).not.toHaveBeenCalled();
    expect(profileQueriedFor).not.toHaveBeenCalled();
  });

  it("ユーザー削除（403 user_not_found）・セッションなしは null（profiles の結果は使わない）", async () => {
    getUser.mockResolvedValue(authError(403, "user_not_found"));
    await expect(fetchMyAccount()).resolves.toBeNull();
    getUser.mockResolvedValue(authError(400, "", "AuthSessionMissingError"));
    await expect(fetchMyAccount()).resolves.toBeNull();
  });

  it("検証済みユーザーが端末のセッションと違えば、そのユーザーの profiles を取り直す", async () => {
    getUser.mockResolvedValue({
      data: { user: { id: "user-2", email: "b@example.com" } },
      error: null,
    });
    await expect(fetchMyAccount()).resolves.toMatchObject({
      userId: "user-2",
      email: "b@example.com",
    });
    expect(profileQueriedFor.mock.calls.map(([id]) => id)).toEqual(["user-1", "user-2"]);
  });

  it("利用停止（403 user_banned）は AccountBannedError（再試行しない）", async () => {
    getUser.mockResolvedValue(authError(403, "user_banned"));
    const error = await fetchMyAccount().catch((e: unknown) => e);
    expect(error).toBeInstanceOf(AccountBannedError);
    expect(isRetryableQueryError(error)).toBe(false);
  });

  it("通信エラーは network_error の ApiError として投げる（ログイン画面へ送らず、1 回だけ再試行）", async () => {
    const networkError = { name: "AuthRetryableFetchError", status: 0, message: "Failed to fetch" };
    getUser.mockResolvedValue({ data: { user: null }, error: networkError });
    const error = await fetchMyAccount().catch((e: unknown) => e);
    expect(error).toBeInstanceOf(ApiError);
    expect(error).toMatchObject({ code: "network_error", cause: networkError });
    expect(isRetryableQueryError(error)).toBe(true);
  });

  it("セッションのリフレッシュが通信失敗なら network_error（null にしてログアウトさせない）", async () => {
    getSession.mockResolvedValue({
      data: { session: null },
      error: { name: "AuthRetryableFetchError", status: 0, message: "Failed to fetch" },
    });
    await expect(fetchMyAccount()).rejects.toMatchObject({ code: "network_error" });
    expect(getUser).not.toHaveBeenCalled();
  });

  it("profiles の読み取り失敗（PostgREST のプレーンなエラー）は ApiError に変換して投げる", async () => {
    getUser.mockResolvedValue({ data: { user: { id: "user-1", email: null } }, error: null });
    maybeSingle.mockResolvedValue({
      data: null,
      error: { code: "", message: "TypeError: Failed to fetch", details: "", hint: "" },
    });
    await expect(fetchMyAccount()).rejects.toMatchObject({ status: 0, code: "network_error" });
  });
});

describe("signOutAndRedirect", () => {
  const replace = vi.fn();
  const networkError = { name: "AuthRetryableFetchError", status: 0, message: "Failed to fetch" };

  /** 二重遷移防止のフラグ（モジュール内の状態）をテストごとに初期化する */
  async function freshModule() {
    vi.resetModules();
    return import("./account");
  }

  beforeEach(() => {
    signOut.mockReset();
    getSession.mockReset();
    replace.mockReset();
    vi.stubGlobal("window", { location: { replace } });
    vi.spyOn(console, "error").mockImplementation(() => undefined);
    vi.spyOn(console, "warn").mockImplementation(() => undefined);
  });

  afterEach(() => {
    vi.unstubAllGlobals();
    vi.restoreAllMocks();
  });

  it("成功したらログイン画面へ（理由と戻り先を付ける）", async () => {
    const { signOutAndRedirect } = await freshModule();
    signOut.mockResolvedValue({ error: null });
    await expect(signOutAndRedirect({ reason: "session", next: "/dm" })).resolves.toEqual({
      error: null,
    });
    expect(signOut).toHaveBeenCalledWith({ scope: "local" });
    expect(replace).toHaveBeenCalledWith("/login?error=session&next=%2Fdm");
  });

  it("失効要求が通信失敗でも、この端末のセッションが消えていればログアウト済みとしてログイン画面へ", async () => {
    const { signOutAndRedirect } = await freshModule();
    // supabase-js は /logout の通信失敗時もこの端末のセッションを消してからエラーを返す
    signOut.mockResolvedValue({ error: networkError });
    getSession.mockResolvedValue({ data: { session: null }, error: null });
    await expect(signOutAndRedirect()).resolves.toEqual({ error: null });
    expect(replace).toHaveBeenCalledWith("/login");
  });

  it("この端末にセッションが残っている・確認できないなら失敗を返し、画面は移動しない（再試行できる）", async () => {
    const { signOutAndRedirect } = await freshModule();
    signOut.mockResolvedValue({ error: networkError });
    getSession.mockResolvedValue({ data: { session: SESSION }, error: null });
    await expect(signOutAndRedirect()).resolves.toEqual({ error: networkError });
    getSession.mockResolvedValue({ data: { session: null }, error: networkError });
    await expect(signOutAndRedirect()).resolves.toEqual({ error: networkError });
    getSession.mockRejectedValue(new Error("lock timeout"));
    await expect(signOutAndRedirect()).resolves.toEqual({ error: networkError });
    expect(replace).not.toHaveBeenCalled();
  });

  it("global（退会時の全端末の無効化）の失敗は、端末のセッションが消えていても呼び出し元へ返す", async () => {
    const { signOutAndRedirect } = await freshModule();
    signOut.mockResolvedValue({ error: networkError });
    getSession.mockResolvedValue({ data: { session: null }, error: null });
    await expect(signOutAndRedirect({ reason: "withdrawn", scope: "global" })).resolves.toEqual({
      error: networkError,
    });
    expect(replace).not.toHaveBeenCalled();
    // 呼び出し元（退会画面）が続けて local で呼べば、この端末からはログアウトしてログイン画面へ
    signOut.mockResolvedValue({ error: null });
    await signOutAndRedirect({ reason: "withdrawn", scope: "local" });
    expect(replace).toHaveBeenCalledWith("/login?error=withdrawn");
  });
});
