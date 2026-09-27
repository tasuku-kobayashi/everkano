import { useEffect, useRef, type ReactNode } from "react";
import clsx from "clsx";

interface Props {
  open: boolean;
  onClose: () => void;
  title?: string;
  children: ReactNode;
  wide?: boolean;
  testId?: string;
}

/** Accessible dialog: Esc closes, focus moves inside, background is inert for the pointer. */
export function Modal({ open, onClose, title, children, wide, testId }: Props) {
  const ref = useRef<HTMLDivElement>(null);
  useEffect(() => {
    if (!open) return;
    const previous = document.activeElement as HTMLElement | null;
    const first = ref.current?.querySelector<HTMLElement>("button, [href], input, select, textarea, [tabindex]:not([tabindex='-1'])");
    first?.focus();
    const onKey = (e: KeyboardEvent) => {
      if (e.key === "Escape") {
        e.stopPropagation();
        onClose();
      }
    };
    document.addEventListener("keydown", onKey);
    return () => {
      document.removeEventListener("keydown", onKey);
      previous?.focus();
    };
  }, [open, onClose]);
  if (!open) return null;
  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/70 p-4" onMouseDown={(e) => e.target === e.currentTarget && onClose()}>
      <div
        ref={ref}
        role="dialog"
        aria-modal="true"
        aria-label={title}
        data-testid={testId}
        className={clsx("card max-h-[92vh] w-full overflow-auto p-4 shadow-2xl", wide ? "max-w-6xl" : "max-w-xl")}
      >
        {title && (
          <div className="mb-3 flex items-center justify-between">
            <h2 className="text-base font-semibold">{title}</h2>
            <button className="btn-ghost" onClick={onClose} aria-label="閉じる">
              ✕ <span className="kbd">Esc</span>
            </button>
          </div>
        )}
        {children}
      </div>
    </div>
  );
}
