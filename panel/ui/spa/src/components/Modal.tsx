// Modal reutilizable (UI.1) — sustituye window.confirm() global.
// Variantes: confirm | alert | custom (children libres).

import { useEffect, useRef } from "react";

export interface ModalProps {
  open: boolean;
  title: string;
  children?: React.ReactNode;
  // confirm: muestra botones Confirmar/Cancelar; alert: solo Cerrar.
  variant?: "confirm" | "alert" | "custom";
  confirmLabel?: string;
  cancelLabel?: string;
  danger?: boolean;
  busy?: boolean;
  onConfirm?: () => void;
  onCancel: () => void;
}

export function Modal({
  open,
  title,
  children,
  variant = "confirm",
  confirmLabel = "Confirmar",
  cancelLabel = "Cancelar",
  danger = false,
  busy = false,
  onConfirm,
  onCancel,
}: ModalProps) {
  const dialogRef = useRef<HTMLDivElement>(null);

  // Esc cierra el modal; click fuera del dialog también.
  useEffect(() => {
    if (!open) return;
    const onKey = (e: KeyboardEvent) => {
      if (e.key === "Escape") onCancel();
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [open, onCancel]);

  // Click fuera del dialog (en el overlay) cierra.
  const onOverlayClick = (e: React.MouseEvent) => {
    if (e.target === e.currentTarget) onCancel();
  };

  if (!open) return null;
  return (
    <div
      className="modal-overlay"
      onClick={onOverlayClick}
      role="dialog"
      aria-modal="true"
      aria-labelledby="modal-title"
    >
      <div className="modal-dialog" ref={dialogRef}>
        <h3 id="modal-title" className="modal-title">{title}</h3>
        {children && <div className="modal-body">{children}</div>}
        <div className="modal-actions">
          <button onClick={onCancel} disabled={busy}>{cancelLabel}</button>
          {variant === "confirm" && (
            <button
              className={danger ? "danger" : "primary"}
              onClick={onConfirm}
              disabled={busy}
            >
              {busy ? "…" : confirmLabel}
            </button>
          )}
          {variant === "alert" && (
            <button className="primary" onClick={onCancel} disabled={busy}>
              Cerrar
            </button>
          )}
        </div>
      </div>
    </div>
  );
}
