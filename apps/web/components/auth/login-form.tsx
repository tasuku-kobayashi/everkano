"use client";

import { useEffect, useRef, useState, type FormEvent } from "react";
import { Button } from "@/components/ui/button";
import { MailIcon } from "@/components/ui/icons";
import { Wordmark } from "@/components/ui/wordmark";
import { API_ERROR_MESSAGES } from "@/lib/api/errors";
import { authErrorMessage, INVALID_EMAIL_MESSAGE, isValidEmail } from "@/lib/auth/errors";
import {
  clearPendingLogin,
  loadPendingLogin,
  parseResendWaitSeconds,
  RESEND_COOLDOWN_SECONDS,
  resendCooldownRemaining,
  savePendingLogin,
} from "@/lib/auth/pending-login";
import {
  emailRedirectUrl,
  isSessionEndingLoginError,
  LOGIN_ERROR_MESSAGES,
  type LoginErrorReason,
} from "@/lib/auth/redirect";
import { isProfileWithdrawn } from "@/lib/auth/withdrawn";
import { cn } from "@/lib/cn";
import { getPublicEnv } from "@/lib/env";
import { getSupabaseBrowserClient } from "@/lib/supabase/client";

const OTP_LENGTH = 6;

const NETWORK_ERROR_MESSAGE = API_ERROR_MESSAGES.network_error;
/** 送信間隔の制限に当たったが、直前に送ったメールのコードが使える場合の案内 */
const ALREADY_SENT_NOTICE =
  "確認コードは送信済みです。届いているメールの6桁のコードを入力してください。新しいコードはしばらくしてから再送信できます。";

export interface LoginFormProps {
  initialError: LoginErrorReason | null;
  /** ログイン後の遷移先（検証済みの相対パス） */
  nextPath: string;
}

type Step = "email" | "code";

type SendResult =
  | { status: "sent" }
  /** 送信間隔の制限（over_email_send_rate_limit）。waitSeconds が読めたら、このアドレスには送信済み */
  | { status: "throttled"; waitSeconds: number | null; message: string }
  | { status: "failed" };

/**
 * ログイン画面（仕様 §5.1）。
 * 1. メールアドレス → signInWithOtp でマジックリンク（+ 6 桁コード）を送信
 * 2. 「メールを確認してください」: メールのリンク（/auth/confirm）か、6 桁コードを入力して verifyOtp
 *    どちらでもログイン後は nextPath（ログイン前に開こうとしていたページ）へ遷移する
 *    ※ iOS のホーム画面 PWA は Safari と Cookie を共有しないため、PWA からはコード入力でログインする
 *
 * コード入力待ちの状態（メールアドレス・送信時刻）は端末に保存し（lib/auth/pending-login.ts）、
 * メールアプリへ切り替えている間に iOS がアプリを再起動しても、戻ってきたらコード入力画面から続けられる。
 * 手元にコードがある場合は、メール入力画面の「確認コードをお持ちの場合」から直接コード入力へ進める。
 */
export function LoginForm({ initialError, nextPath }: LoginFormProps) {
  const [step, setStep] = useState<Step>("email");
  const [email, setEmail] = useState("");
  const [code, setCode] = useState("");
  const [sending, setSending] = useState(false);
  const [verifying, setVerifying] = useState(false);
  const [error, setError] = useState<string | null>(
    initialError ? LOGIN_ERROR_MESSAGES[initialError] : null,
  );
  /** エラーではない案内（送信済みのコードを使ってください 等） */
  const [notice, setNotice] = useState<string | null>(null);
  const [cooldown, setCooldown] = useState(0);
  const codeInputRef = useRef<HTMLInputElement>(null);
  const autoSubmittedRef = useRef<string | null>(null);

  // 退会済み・セッション無効・利用停止で戻ってきた場合、念のためこの端末のセッションを破棄する
  // （Cookie が残っていると、ログイン画面から / へ戻されてリダイレクトが繰り返されるため）。
  // それ以外は、前回のコード入力待ち（アプリの再起動で消えた画面）を復元する。
  useEffect(() => {
    if (isSessionEndingLoginError(initialError)) {
      clearPendingLogin();
      void getSupabaseBrowserClient()
        .auth.signOut({ scope: "local" })
        .then(({ error: signOutError }) => {
          if (signOutError) console.warn("[login] sign out failed:", signOutError.message);
        });
      return;
    }
    const pending = loadPendingLogin();
    if (!pending) return;
    setEmail(pending.email);
    setCode("");
    setStep("code");
    setCooldown(resendCooldownRemaining(pending.sentAt));
  }, [initialError]);

  useEffect(() => {
    if (cooldown <= 0) return;
    const timer = setTimeout(() => setCooldown((c) => c - 1), 1000);
    return () => clearTimeout(timer);
  }, [cooldown]);

  useEffect(() => {
    if (step === "code") codeInputRef.current?.focus();
  }, [step]);

  async function sendLink(targetEmail: string): Promise<SendResult> {
    setSending(true);
    setError(null);
    setNotice(null);
    try {
      const siteUrl = getPublicEnv().siteUrl ?? window.location.origin;
      const { error: otpError } = await getSupabaseBrowserClient().auth.signInWithOtp({
        email: targetEmail,
        options: {
          // ログイン前に開こうとしていたページ（?next=）をメールのリンクまで運ぶ
          // （/auth/callback?next=.. → メールのリンクの redirect_to → /auth/confirm が取り出す）
          emailRedirectTo: emailRedirectUrl(siteUrl, nextPath),
          shouldCreateUser: true,
        },
      });
      if (otpError) {
        console.warn("[login] signInWithOtp failed:", otpError.code, otpError.message);
        const message = authErrorMessage(otpError, "send");
        if (otpError.code === "over_email_send_rate_limit") {
          return {
            status: "throttled",
            waitSeconds: parseResendWaitSeconds(otpError.message),
            message,
          };
        }
        setError(message);
        return { status: "failed" };
      }
      savePendingLogin(targetEmail);
      setCooldown(RESEND_COOLDOWN_SECONDS);
      return { status: "sent" };
    } catch (cause) {
      console.error("[login] signInWithOtp threw:", cause);
      setError(NETWORK_ERROR_MESSAGE);
      return { status: "failed" };
    } finally {
      setSending(false);
    }
  }

  function enterCodeStep(targetEmail: string) {
    setEmail(targetEmail);
    setCode("");
    autoSubmittedRef.current = null;
    setStep("code");
  }

  async function onSubmitEmail(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    const trimmed = email.trim();
    if (!isValidEmail(trimmed)) {
      setError(INVALID_EMAIL_MESSAGE);
      return;
    }
    const result = await sendLink(trimmed);
    if (result.status === "sent") {
      enterCodeStep(trimmed);
      return;
    }
    if (result.status !== "throttled") return;

    // 送信間隔の制限: このアドレスには直前に送っている（= 有効なコードが届いている）なら、コード入力へ進める。
    // 「上限に達しました」だけを出すと、コードを持っているのに入力する手段が無くなる（iOS PWA の再起動後など）
    const pending = loadPendingLogin();
    const samePending = pending?.email.toLowerCase() === trimmed.toLowerCase() ? pending : null;
    if (result.waitSeconds === null && !samePending) {
      // プロジェクト全体の送信上限など: メールは送られていない
      setError(result.message);
      return;
    }
    if (!samePending) savePendingLogin(trimmed);
    enterCodeStep(trimmed);
    setCooldown(
      result.waitSeconds ?? (samePending ? resendCooldownRemaining(samePending.sentAt) : 0),
    );
    setNotice(ALREADY_SENT_NOTICE);
  }

  /** 「確認コードをお持ちの場合」: 送信せずにコード入力へ進む（届いているメールのコードを使う） */
  function onHaveCode() {
    const trimmed = email.trim();
    if (!isValidEmail(trimmed)) {
      setError("メールアドレスを入力してから、確認コードを入力してください。");
      return;
    }
    setError(null);
    setNotice(null);
    enterCodeStep(trimmed);
  }

  async function onResend() {
    autoSubmittedRef.current = null;
    const result = await sendLink(email);
    if (result.status === "throttled") {
      setCooldown(result.waitSeconds ?? RESEND_COOLDOWN_SECONDS);
      setNotice(ALREADY_SENT_NOTICE);
    }
  }

  function onChangeEmailAddress() {
    clearPendingLogin();
    setStep("email");
    setError(null);
    setNotice(null);
    setCode("");
  }

  async function verify(token: string) {
    if (verifying) return;
    setVerifying(true);
    setError(null);
    setNotice(null);
    let leaving = false;
    try {
      const supabase = getSupabaseBrowserClient();
      const { data, error: verifyError } = await supabase.auth.verifyOtp({
        email,
        token,
        type: "email",
      });
      if (verifyError || !data.user) {
        console.warn("[login] verifyOtp failed:", verifyError?.code, verifyError?.message);
        setError(
          verifyError
            ? authErrorMessage(verifyError, "verify")
            : "ログインできませんでした。再度お試しください。",
        );
        return;
      }
      clearPendingLogin();
      if (await isProfileWithdrawn(supabase, data.user.id)) {
        await supabase.auth.signOut({ scope: "local" });
        setError(LOGIN_ERROR_MESSAGES.withdrawn);
        setStep("email");
        return;
      }
      // ハードナビゲーションで遷移し、このタブに残っているクライアント状態（前にログインしていたユーザーの
      // React Query のキャッシュ・アプリ内の戻る履歴の数など）を完全に破棄する。
      // router.replace だとセッション切れで /login へソフトに戻された場合に前のユーザーのキャッシュ
      // （メールアドレス・会話 ID・DM 本文など）が新しいユーザーに見えてしまう
      leaving = true;
      window.location.replace(nextPath);
    } catch (cause) {
      console.error("[login] verifyOtp threw:", cause);
      setError(NETWORK_ERROR_MESSAGE);
    } finally {
      // 遷移中はボタンを「読み込み中」のままにする（二重送信防止）
      if (!leaving) setVerifying(false);
    }
  }

  function onSubmitCode(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    if (code.length === OTP_LENGTH) void verify(code);
  }

  function onChangeCode(value: string) {
    setCode(value.replace(/\D/g, "").slice(0, OTP_LENGTH));
  }

  // 6 桁そろったら自動でログイン（同じコードでは 1 回だけ）。
  // 検証中に別のコードが入力・貼り付けされた場合は、いまの検証が終わってから送る。
  const verifyRef = useRef(verify);
  verifyRef.current = verify;
  useEffect(() => {
    if (step !== "code" || verifying) return;
    if (code.length === OTP_LENGTH && autoSubmittedRef.current !== code) {
      autoSubmittedRef.current = code;
      void verifyRef.current(code);
    }
  }, [code, step, verifying]);

  return (
    <main className="mx-auto flex min-h-dvh w-full max-w-[480px] flex-col px-8 pt-safe pb-safe">
      <div className="flex flex-1 flex-col justify-center py-10">
        {step === "email" ? (
          <form onSubmit={onSubmitEmail} noValidate className="flex flex-col items-stretch">
            <h1 className="sr-only">everkano にログイン</h1>
            <div className="mb-3 flex justify-center">
              <Wordmark height={56} />
            </div>
            <p className="mb-8 text-center text-[15px] leading-5 font-semibold text-ig-secondary">
              AIキャラクターたちの毎日をのぞいて、
              <br />
              DMで話そう。
            </p>

            <label htmlFor="login-email" className="sr-only">
              メールアドレス
            </label>
            <input
              id="login-email"
              type="email"
              inputMode="email"
              autoComplete="email"
              autoCapitalize="none"
              autoCorrect="off"
              spellCheck={false}
              placeholder="メールアドレス"
              value={email}
              onChange={(event) => setEmail(event.target.value)}
              aria-invalid={Boolean(error)}
              aria-describedby={error ? "login-error" : undefined}
              className="h-12 w-full rounded-xl border border-ig-input-border bg-ig-input-bg px-4 text-[16px] text-ig-text outline-none placeholder:text-ig-secondary focus:border-ig-secondary"
            />

            <Button
              type="submit"
              size="lg"
              fullWidth
              className="mt-3"
              loading={sending}
              disabled={email.trim().length === 0}
            >
              ログインリンクを送信
            </Button>

            <ErrorText message={error} />

            <Button
              variant="ghost"
              size="sm"
              className="mt-4 self-center"
              disabled={sending}
              onClick={onHaveCode}
            >
              確認コードをお持ちの場合
            </Button>

            <p className="mt-6 text-center text-[12px] leading-4 text-ig-secondary">
              アカウントをお持ちでない場合も、メールアドレスを入力するだけで始められます。
            </p>
          </form>
        ) : (
          <form
            onSubmit={onSubmitCode}
            noValidate
            className="flex flex-col items-center text-center"
          >
            <div className="mb-4 flex size-[88px] items-center justify-center rounded-full border-2 border-ig-text">
              <MailIcon size={44} strokeWidth={1.4} />
            </div>
            <h1 className="text-[18px] leading-6 font-semibold">メールを確認してください</h1>
            <p className="mt-2 text-[14px] leading-[18px] text-ig-secondary">
              <span className="font-semibold break-all text-ig-text">{email}</span>{" "}
              に送ったログインメールのリンクをタップするか、記載されている6桁の確認コードを入力してください。
            </p>

            <label htmlFor="login-code" className="sr-only">
              確認コード（6桁）
            </label>
            <input
              ref={codeInputRef}
              id="login-code"
              type="text"
              inputMode="numeric"
              autoComplete="one-time-code"
              pattern="[0-9]*"
              maxLength={OTP_LENGTH}
              placeholder="確認コード"
              value={code}
              onChange={(event) => onChangeCode(event.target.value)}
              aria-invalid={Boolean(error)}
              aria-describedby={error ? "login-error" : notice ? "login-notice" : undefined}
              className={cn(
                "mt-6 h-14 w-full rounded-xl border border-ig-input-border bg-ig-input-bg px-4 text-center text-[24px] font-semibold tracking-[0.5em] text-ig-text outline-none focus:border-ig-secondary",
                "placeholder:text-[16px] placeholder:font-normal placeholder:tracking-normal placeholder:text-ig-secondary",
              )}
            />

            <Button
              type="submit"
              size="lg"
              fullWidth
              className="mt-3"
              loading={verifying}
              disabled={code.length !== OTP_LENGTH}
            >
              コードでログイン
            </Button>

            <ErrorText message={error} />
            <NoticeText message={error ? null : notice} />

            <div className="mt-6 flex flex-col items-center gap-1">
              <Button
                variant="ghost"
                size="sm"
                disabled={cooldown > 0 || sending}
                loading={sending}
                onClick={() => void onResend()}
              >
                {cooldown > 0 ? `コードを再送信（${cooldown}秒）` : "コードを再送信"}
              </Button>
              <Button
                variant="ghost"
                size="sm"
                className="text-ig-text"
                onClick={onChangeEmailAddress}
              >
                メールアドレスを変更
              </Button>
            </div>

            <p className="mt-6 text-[12px] leading-4 text-ig-secondary">
              ホーム画面に追加したアプリをお使いの場合は、確認コードでログインしてください。
            </p>
          </form>
        )}
      </div>
    </main>
  );
}

function ErrorText({ message }: { message: string | null }) {
  if (!message) return null;
  return (
    <p
      id="login-error"
      role="alert"
      className="mt-4 text-center text-[14px] leading-[18px] text-ig-red-text"
    >
      {message}
    </p>
  );
}

function NoticeText({ message }: { message: string | null }) {
  if (!message) return null;
  return (
    <p
      id="login-notice"
      role="status"
      className="mt-4 text-center text-[14px] leading-[18px] text-ig-text"
    >
      {message}
    </p>
  );
}
