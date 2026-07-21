// Modal reutilizable (UI.1) — sustituye window.confirm() global.
// Variantes:
//   - "confirm": muestra Cancelar + botón de confirmar (requiere onConfirm)
//   - "alert":   muestra solo Cerrar (onCancel se reusa para cerrar)
//   - "custom":  NO renderiza el action bar — los children traen sus
//                propios botones (este es el caso para formularios)
//
// Si variant se omite: defaults a "custom". Si pasas onConfirm sin
// variant, se infiere "confirm".

import { useEffect, useRef } from "react";

export interface ModalProps {
  open: boolean;
  title: string;
  children?: React.ReactNode;
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
  variant,
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

  // Inferir variante si no se pasa: onConfirm → confirm, sino custom.
  const inferredVariant: "confirm" | "alert" | "custom" =
    variant ?? (onConfirm ? "confirm" : "custom");

  // Solo las variantes confirm/alert renderizan el action bar.
  // "custom" deja los actions a los children (forms con sus botones).
  const showActionBar = inferredVariant !== "custom";

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
        {showActionBar && (
          <div className="modal-actions">
            {inferredVariant === "alert" ? (
              // Alert: solo Cerrar.
              <button
                className="primary"
                onClick={onCancel}
                disabled={busy}
              >
                Cerrar
              </button>
            ) : (
              // Confirm: Cancelar + acción principal.
              <>
                <button onClick={onCancel} disabled={busy}>
                  {cancelLabel}
                </button>
                <button
                  className={danger ? "danger" : "primary"}
                  onClick={onConfirm}
                  disabled={busy}
                >
                  {busy ? "…" : confirmLabel}
                </button>
              </>
            )}
          </div>
        )}
      </div>
    </div>
  );
}
