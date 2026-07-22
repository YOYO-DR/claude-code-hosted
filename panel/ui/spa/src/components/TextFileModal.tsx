// Visor de archivos de texto full-screen. Abre cuando el explorador detecta
// un archivo NO binario y el usuario hace click. Se complementa con el visor
// de imágenes: ambos comparten la estética (overlay oscuro, header, footer).
//
// Funcionalidad:
//   - Contenido en <pre> monoespaciado, scrollable, wrap configurable.
//   - Botón Copiar (clipboard API) — copia todo el contenido visible.
//   - Botón Descargar — apunta a /raw/ (con Content-Type correcto) y usa el
//     atributo `download` para forzar la descarga.
//   - Indicador "(truncado)" si /file/ devolvió solo una porción (cap 100 KB).
//   - Cerrar con ESC, click fuera del contenido, o ×.

import { useEffect, useRef, useState } from "react";

export interface TextFileModalProps {
  open: boolean;
  content: string;
  filename: string;
  sizeBytes?: number;
  truncated?: boolean;
  downloadUrl: string;
  onClose: () => void;
}

function fmtSize(b?: number): string {
  if (b === undefined) return "";
  if (b < 1024) return `${b} B`;
  if (b < 1024 * 1024) return `${(b / 1024).toFixed(1)} KB`;
  return `${(b / 1024 / 1024).toFixed(1)} MB`;
}

export function TextFileModal({
  open, content, filename, sizeBytes, truncated, downloadUrl, onClose,
}: TextFileModalProps) {
  const [copied, setCopied] = useState(false);
  const [wrap, setWrap] = useState(true);
  const copyTimer = useRef<number | null>(null);

  // Reset al cambiar de archivo / abrir / cerrar.
  useEffect(() => {
    if (open) {
      setCopied(false);
      if (copyTimer.current) {
        window.clearTimeout(copyTimer.current);
        copyTimer.current = null;
      }
    }
    return () => {
      if (copyTimer.current) window.clearTimeout(copyTimer.current);
    };
  }, [open, filename]);

  // ESC cierra.
  useEffect(() => {
    if (!open) return;
    const onKey = (e: KeyboardEvent) => {
      if (e.key === "Escape") onClose();
      else if ((e.ctrlKey || e.metaKey) && e.key.toLowerCase() === "c" && window.getSelection()?.toString() === "") {
        // Sin selección: Ctrl/Cmd+C copia TODO el contenido (atajo extra).
        e.preventDefault();
        copyAll();
      }
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [open, onClose, content]);

  // Bloquear scroll del body.
  useEffect(() => {
    if (!open) return;
    const prev = document.body.style.overflow;
    document.body.style.overflow = "hidden";
    return () => {
      document.body.style.overflow = prev;
    };
  }, [open]);

  const copyAll = async () => {
    try {
      await navigator.clipboard.writeText(content);
      setCopied(true);
      if (copyTimer.current) window.clearTimeout(copyTimer.current);
      copyTimer.current = window.setTimeout(() => setCopied(false), 1500);
    } catch {
      // Fallback ultra-básico: select + execCommand (deprecated pero
      // todavía funciona en navegadores viejos si el clipboard API falla).
      const ta = document.createElement("textarea");
      ta.value = content;
      ta.style.position = "fixed";
      ta.style.opacity = "0";
      document.body.appendChild(ta);
      ta.select();
      try { document.execCommand("copy"); setCopied(true); } catch { /* ignore */ }
      document.body.removeChild(ta);
    }
  };

  if (!open) return null;

  return (
    <div className="img-modal-overlay" onClick={onClose} role="dialog" aria-modal="true" aria-label={filename}>
      <header className="img-modal-header" onClick={(e) => e.stopPropagation()}>
        <span className="img-modal-title" title={filename}>
          📄 {filename}
        </span>
        <span className="img-modal-info">
          {fmtSize(sizeBytes)}
          {truncated && (
            <span style={{ marginLeft: "0.5rem", color: "var(--warn-fg, #4a3700)" }}>
              ⚠ truncado a 100 KB
            </span>
          )}
          {content && (
            <span style={{ marginLeft: "0.5rem" }}>
              {content.length.toLocaleString()} chars
            </span>
          )}
        </span>
        <button className="img-modal-close" onClick={onClose} title="Cerrar (Esc)" aria-label="Cerrar">×</button>
      </header>

      <div
        className="text-modal-stage"
        onClick={(e) => { if (e.target === e.currentTarget) onClose(); }}
      >
        <pre
          className={`text-modal-pre${wrap ? " wrap" : ""}`}
          onClick={(e) => e.stopPropagation()}
        >
          {content || "(vacío)"}
        </pre>
      </div>

      <footer className="img-modal-controls" onClick={(e) => e.stopPropagation()}>
        <button
          onClick={copyAll}
          title="Copiar todo el contenido"
          className={copied ? "ok" : ""}
        >
          {copied ? "✓ Copiado" : "📋 Copiar"}
        </button>
        <button onClick={() => setWrap((w) => !w)} title="Alternar wrap de líneas">
          {wrap ? "↩ Wrap" : "→ No wrap"}
        </button>
        <a className="img-modal-download" href={downloadUrl} download={filename} title="Descargar">
          ⬇ Descargar
        </a>
      </footer>
    </div>
  );
}