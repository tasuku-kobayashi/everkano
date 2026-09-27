import { useEffect } from "react";

/** Global shortcuts. Ignored while typing in inputs / textareas / selects. */
export function useShortcuts(handlers: Record<string, (event: KeyboardEvent) => void>, enabled = true): void {
  useEffect(() => {
    if (!enabled) return;
    const onKey = (event: KeyboardEvent) => {
      const target = event.target as HTMLElement | null;
      const tag = target?.tagName;
      const typing = tag === "INPUT" || tag === "TEXTAREA" || tag === "SELECT" || target?.isContentEditable;
      if (typing && event.key !== "Escape") return;
      const handler = handlers[event.key] ?? handlers[event.key.toLowerCase()];
      if (handler) {
        handler(event);
      }
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [handlers, enabled]);
}
