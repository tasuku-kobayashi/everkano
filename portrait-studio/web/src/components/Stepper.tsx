import clsx from "clsx";

export function Stepper({ steps, current, onSelect }: { steps: string[]; current: number; onSelect?: (index: number) => void }) {
  return (
    <ol className="sticky top-0 z-20 flex items-center gap-2 border-b border-ink-700 bg-ink-950/95 px-2 py-2 backdrop-blur" aria-label="作成ステップ">
      {steps.map((label, i) => {
        const state = i < current ? "done" : i === current ? "current" : "todo";
        return (
          <li key={label} className="flex items-center gap-2">
            <button
              type="button"
              disabled={i > current}
              onClick={() => onSelect?.(i)}
              aria-current={state === "current" ? "step" : undefined}
              className={clsx(
                "flex items-center gap-2 rounded-md px-2 py-1 text-sm",
                state === "current" && "bg-accent text-white",
                state === "done" && "text-emerald-300 hover:bg-ink-800",
                state === "todo" && "text-slate-500",
              )}
            >
              <span className={clsx("flex h-5 w-5 items-center justify-center rounded-full border text-xs", state === "current" ? "border-white" : "border-current")}>
                {state === "done" ? "✓" : i + 1}
              </span>
              {label}
            </button>
            {i < steps.length - 1 && <span className="text-slate-600">›</span>}
          </li>
        );
      })}
      <li className="ml-auto text-xs text-slate-500">
        残り {Math.max(0, steps.length - current - 1)} ステップ
      </li>
    </ol>
  );
}
