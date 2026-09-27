import { useRef, useState } from "react";
import clsx from "clsx";

interface Props {
  onFiles: (files: File[]) => void;
  disabled?: boolean;
  label?: string;
}

export function Dropzone({ onFiles, disabled, label = "画像をドラッグ&ドロップ、またはクリックして選択（複数可）" }: Props) {
  const input = useRef<HTMLInputElement>(null);
  const [over, setOver] = useState(false);
  const accept = (list: FileList | null) => {
    if (!list) return;
    const files = Array.from(list).filter((f) => f.type.startsWith("image/"));
    if (files.length) onFiles(files);
  };
  return (
    <div
      role="button"
      tabIndex={0}
      aria-label={label}
      data-testid="dropzone"
      className={clsx(
        "flex cursor-pointer flex-col items-center justify-center rounded-lg border-2 border-dashed p-6 text-center text-sm text-slate-300",
        over ? "border-accent bg-accent-soft/40" : "border-ink-600 hover:border-slate-400",
        disabled && "pointer-events-none opacity-50",
      )}
      onClick={() => input.current?.click()}
      onKeyDown={(e) => (e.key === "Enter" || e.key === " ") && input.current?.click()}
      onDragOver={(e) => {
        e.preventDefault();
        setOver(true);
      }}
      onDragLeave={() => setOver(false)}
      onDrop={(e) => {
        e.preventDefault();
        setOver(false);
        accept(e.dataTransfer.files);
      }}
    >
      <span>{label}</span>
      <span className="mt-1 text-xs text-slate-500">PNG / JPEG / WebP</span>
      <input ref={input} type="file" accept="image/*" multiple className="hidden" onChange={(e) => accept(e.target.files)} data-testid="file-input" />
    </div>
  );
}
