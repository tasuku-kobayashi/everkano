import { createContext, useCallback, useContext, useMemo, useState, type ReactNode } from "react";
import clsx from "clsx";

type Kind = "error" | "success" | "info";
interface ToastItem {
  id: number;
  kind: Kind;
  message: string;
}
interface ToastApi {
  error: (message: string) => void;
  success: (message: string) => void;
  info: (message: string) => void;
}

const ToastContext = createContext<ToastApi | null>(null);
let counter = 0;

export function ToastProvider({ children }: { children: ReactNode }) {
  const [items, setItems] = useState<ToastItem[]>([]);
  const push = useCallback((kind: Kind, message: string) => {
    const id = ++counter;
    setItems((prev) => [...prev, { id, kind, message }]);
    setTimeout(() => setItems((prev) => prev.filter((t) => t.id !== id)), kind === "error" ? 9000 : 4000);
  }, []);
  const value = useMemo<ToastApi>(
    () => ({ error: (m) => push("error", m), success: (m) => push("success", m), info: (m) => push("info", m) }),
    [push],
  );
  return (
    <ToastContext.Provider value={value}>
      {children}
      <div className="pointer-events-none fixed right-4 top-4 z-[100] flex w-96 max-w-[calc(100vw-2rem)] flex-col gap-2" role="status" aria-live="polite">
        {items.map((t) => (
          <div
            key={t.id}
            data-testid={`toast-${t.kind}`}
            className={clsx(
              "pointer-events-auto rounded-md border px-3 py-2 text-sm shadow-lg",
              t.kind === "error" && "border-rose-700 bg-rose-950 text-rose-100",
              t.kind === "success" && "border-emerald-700 bg-emerald-950 text-emerald-100",
              t.kind === "info" && "border-ink-600 bg-ink-800 text-slate-100",
            )}
          >
            <div className="flex items-start gap-2">
              <span className="flex-1 whitespace-pre-wrap break-words">{t.message}</span>
              <button className="text-xs opacity-70 hover:opacity-100" aria-label="閉じる" onClick={() => setItems((prev) => prev.filter((x) => x.id !== t.id))}>
                ✕
              </button>
            </div>
          </div>
        ))}
      </div>
    </ToastContext.Provider>
  );
}

export function useToast(): ToastApi {
  const ctx = useContext(ToastContext);
  if (!ctx) throw new Error("ToastProvider missing");
  return ctx;
}
