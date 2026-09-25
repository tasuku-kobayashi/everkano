"use client";

import {
  createContext,
  useCallback,
  useContext,
  useEffect,
  useMemo,
  useRef,
  useState,
  type ReactNode,
} from "react";
import { cn } from "@/lib/cn";

export type ToastVariant = "default" | "error";

export interface ToastOptions {
  variant?: ToastVariant;
  /** 表示時間（ms）。既定 3000（error は 4000） */
  durationMs?: number;
  /** 右端のテキストボタン（例: 「元に戻す」） */
  action?: { label: string; onClick: () => void };
}

interface ToastItem {
  id: number;
  message: string;
  variant: ToastVariant;
  action: ToastOptions["action"];
}

export interface ToastApi {
  /** トーストを表示する（同時に表示するのは 1 件。新しいものが古いものを置き換える） */
  show: (message: string, options?: ToastOptions) => void;
  /** エラー用のショートカット */
  error: (message: string, options?: Omit<ToastOptions, "variant">) => void;
  dismiss: () => void;
}

const ToastContext = createContext<ToastApi | null>(null);

/**
 * Instagram 風トースト（画面下部・タブバーの上に表示されるダークなバー）。
 * app/providers.tsx でアプリ全体を包んでいる。
 */
export function ToastProvider({ children }: { children: ReactNode }) {
  const [toast, setToast] = useState<ToastItem | null>(null);
  const [visible, setVisible] = useState(false);
  const idRef = useRef(0);
  const hideTimer = useRef<ReturnType<typeof setTimeout> | undefined>(undefined);
  const removeTimer = useRef<ReturnType<typeof setTimeout> | undefined>(undefined);

  const dismiss = useCallback(() => {
    clearTimeout(hideTimer.current);
    setVisible(false);
    clearTimeout(removeTimer.current);
    removeTimer.current = setTimeout(() => setToast(null), 200);
  }, []);

  const show = useCallback(
    (message: string, options: ToastOptions = {}) => {
      const variant = options.variant ?? "default";
      idRef.current += 1;
      clearTimeout(hideTimer.current);
      clearTimeout(removeTimer.current);
      setToast({ id: idRef.current, message, variant, action: options.action });
      setVisible(true);
      hideTimer.current = setTimeout(
        dismiss,
        options.durationMs ?? (variant === "error" ? 4000 : 3000),
      );
    },
    [dismiss],
  );

  const error = useCallback(
    (message: string, options: Omit<ToastOptions, "variant"> = {}) =>
      show(message, { ...options, variant: "error" }),
    [show],
  );

  useEffect(
    () => () => {
      clearTimeout(hideTimer.current);
      clearTimeout(removeTimer.current);
    },
    [],
  );

  const api = useMemo<ToastApi>(() => ({ show, error, dismiss }), [show, error, dismiss]);

  return (
    <ToastContext.Provider value={api}>
      {children}
      <div
        aria-live="polite"
        aria-atomic="true"
        className="bottom-toast pointer-events-none fixed inset-x-0 z-[60] mx-auto flex max-w-[480px] justify-center px-3"
      >
        {toast ? (
          <div
            key={toast.id}
            role={toast.variant === "error" ? "alert" : "status"}
            className={cn(
              "bg-ig-toast text-ig-toast-text pointer-events-auto flex w-full items-center gap-3 rounded-lg px-4 py-3 text-[14px] leading-[18px] shadow-[0_4px_12px_rgb(0_0_0/0.15)] transition-[opacity,transform] duration-200",
              visible ? "translate-y-0 opacity-100" : "translate-y-2 opacity-0",
            )}
          >
            <p className="flex-1">{toast.message}</p>
            {toast.action ? (
              <button
                type="button"
                className="text-ig-blue shrink-0 font-semibold"
                onClick={() => {
                  toast.action?.onClick();
                  dismiss();
                }}
              >
                {toast.action.label}
              </button>
            ) : null}
          </div>
        ) : null}
      </div>
    </ToastContext.Provider>
  );
}

/** トーストを表示する。`const toast = useToast(); toast.show("保存しました")` */
export function useToast(): ToastApi {
  const context = useContext(ToastContext);
  if (!context) throw new Error("useToast() は <ToastProvider> の内側で使ってください");
  return context;
}

/**
 * 画面下部に固定フッター（コメント入力欄・DM 入力欄など）がある画面で、トーストをその上に出すための高さを設定する。
 * 例: useBottomBarHeight(56)  // 入力欄の高さ（safe-area を除く）
 */
export function useBottomBarHeight(px: number): void {
  useEffect(() => {
    const root = document.documentElement;
    root.style.setProperty("--bottom-bar-h", `${px}px`);
    return () => {
      root.style.removeProperty("--bottom-bar-h");
    };
  }, [px]);
}
