// Visor de diffs de git full-screen. SP16 originalmente abría el diff en
// TextFileModal, pero quedaba como salida cruda de terminal. Este modal
// colorea cada línea para que el archivo entero se lea como en el cliente
// de git: verde para +, rojo para -, gris para contexto, marcador para
// los hunk headers (@@) y atenuado para los metadata (diff --git / index).
//
// Sin wrap: la alineación de + / - / espacio se rompe si las líneas largas
// se envuelven. El usuario puede hacer scroll horizontal si quiere.

import { useEffect, useMemo, useRef, useState } from "react";

export interface DiffModalProps {
  open: boolean;
  diff: string;
  filename: string;            // ruta del archivo, ej "panel/foo.py"
  additions: number;
  deletions: number;
  onClose: () => void;
}

type Kind = "meta" | "hunk" | "add" | "del" | "ctx" | "empty";

function classify(line: string): Kind {
  if (line.length === 0) return "empty";
  if (line.startsWith("diff --git") || line.startsWith("index ") ||
      line.startsWith("--- ") || line.startsWith("+++ ") ||
      line.startsWith("new file") || line.startsWith("deleted file") ||
      line.startsWith("old mode") || line.startsWith("new mode") ||
      line.startsWith("similarity") || line.startsWith("rename ") ||
      line.startsWith("copy ") || line.startsWith("Binary files")) {
    return "meta";
  }
  if (line.startsWith("@@")) return "hunk";
  if (line.startsWith("+")) return "add";
  if (line.startsWith("-")) return "del";
  return "ctx";
}

/** Muestra el primer carácter (signo) en su propio gutter, y el resto del
 *  texto. Si la línea es "+xxx" o "-xxx", el gutter es + o - coloreado; si es
 *  contexto, el gutter es un espacio. El texto NUNCA se recorta. */
function DiffLine({ raw }: { raw: string }) {
  const kind = classify(raw);
  if (kind === "empty") return <div className="df-row df-empty">&nbsp;</div>;
  if (kind === "meta") return <div className="df-row df-meta">{raw}</div>;
  if (kind === "hunk") return <div className="df-row df-hunk">{raw}</div>;
  const sign = kind === "add" ? "+" : kind === "del" ? "-" : " ";
  const body = raw.slice(1);
  return (
    <div className={`df-row df-${kind}`}>
      <span className="df-sign" aria-hidden>{sign}</span>
      <span className="df-text">{body || " "}</span>
    </div>
  );
}

export function DiffModal({
  open, diff, filename, additions, deletions, onClose,
}: DiffModalProps) {
  const [copied, setCopied] = useState(false);
  const copyTimer = useRef<number | null>(null);

  // URL para Descargar: Blob URL en memoria. El endpoint raw no existe para
  // diffs (el texto viaja dentro de /diff/file/), así que lo generamos aquí.
  const [downloadUrl, setDownloadUrl] = useState<string>("");
  useEffect(() => {
    if (!open || typeof Blob === "undefined") return;
    const blob = new Blob([diff], { type: "text/plain;charset=utf-8" });
    const u = URL.createObjectURL(blob);
    setDownloadUrl(u);
    return () => URL.revokeObjectURL(u);
  }, [diff, open]);

  // Reset al cambiar de archivo / abrir.
  useEffect(() => {
    if (open) setCopied(false);
    return () => { if (copyTimer.current) window.clearTimeout(copyTimer.current); };
  }, [open, filename]);

  // ESC cierra; bloquear scroll del body.
  useEffect(() => {
    if (!open) return;
    const onKey = (e: KeyboardEvent) => { if (e.key === "Escape") onClose(); };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [open, onClose]);
  useEffect(() => {
    if (!open) return;
    const prev = document.body.style.overflow;
    document.body.style.overflow = "hidden";
    return () => { document.body.style.overflow = prev; };
  }, [open]);

  const lines = useMemo(() => (diff || "").split("\n"), [diff]);

  const copyAll = async () => {
    try { await navigator.clipboard.writeText(diff); }
    catch {
      const ta = document.createElement("textarea");
      ta.value = diff; ta.style.position = "fixed"; ta.style.opacity = "0";
      document.body.appendChild(ta); ta.select();
      try { document.execCommand("copy"); } catch { /* ignore */ }
      document.body.removeChild(ta);
    }
    setCopied(true);
    if (copyTimer.current) window.clearTimeout(copyTimer.current);
    copyTimer.current = window.setTimeout(() => setCopied(false), 1500);
  };

  if (!open) return null;
  return (
    <div className="img-modal-overlay" onClick={onClose} role="dialog" aria-modal="true" aria-label={filename}>
      <header className="img-modal-header" onClick={(e) => e.stopPropagation()}>
        <span className="img-modal-title" title={filename}>📝 {filename}</span>
        <span className="img-modal-info">
          <span style={{ color: "var(--ok-fg)" }}>+{additions}</span>
          {" "}
          <span style={{ color: "var(--err-fg)" }}>−{deletions}</span>
          {" · "}
          {lines.length.toLocaleString()} líneas
        </span>
        <button className="img-modal-close" onClick={onClose} title="Cerrar (Esc)" aria-label="Cerrar">×</button>
      </header>

      <div
        className="diff-modal-stage"
        onClick={(e) => { if (e.target === e.currentTarget) onClose(); }}
      >
        <pre className="diff-modal-pre" onClick={(e) => e.stopPropagation()}>
          {lines.map((ln, i) => <DiffLine key={i} raw={ln} />)}
        </pre>
      </div>

      <footer className="img-modal-controls" onClick={(e) => e.stopPropagation()}>
        <button onClick={copyAll} className={copied ? "ok" : ""}>
          {copied ? "✓ Copiado" : "📋 Copiar"}
        </button>
        {downloadUrl && (
          <a className="img-modal-download" href={downloadUrl} download={`${filename}.diff`} title="Descargar">
            ⬇ Descargar
          </a>
        )}
      </footer>
    </div>
  );
}
