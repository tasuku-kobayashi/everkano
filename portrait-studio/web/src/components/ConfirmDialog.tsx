import { useState, type ReactNode } from "react";
import { Modal } from "./Modal";

interface Props {
  open: boolean;
  title: string;
  danger?: boolean;
  confirmLabel?: string;
  onConfirm: () => void | Promise<void>;
  onCancel: () => void;
  children: ReactNode;
}

/** Dangerous operations always spell out what breaks before the button is enabled. */
export function ConfirmDialog({ open, title, danger = true, confirmLabel = "実行する", onConfirm, onCancel, children }: Props) {
  const [busy, setBusy] = useState(false);
  return (
    <Modal open={open} onClose={onCancel} title={title} testId="confirm-dialog">
      <div className="space-y-3 text-sm">{children}</div>
      <div className="mt-4 flex justify-end gap-2">
        <button className="btn-secondary" onClick={onCancel}>
          キャンセル
        </button>
        <button
          className={danger ? "btn-danger" : "btn-primary"}
          disabled={busy}
          data-testid="confirm-button"
          onClick={async () => {
            setBusy(true);
            try {
              await onConfirm();
            } finally {
              setBusy(false);
            }
          }}
        >
          {confirmLabel}
        </button>
      </div>
    </Modal>
  );
}
