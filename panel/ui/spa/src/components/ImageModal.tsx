// Visor de imágenes full-screen. Abre cuando el explorador de archivos
// detecta un .png/.jpg/etc. y el usuario hace click.
//
// Funcionalidad:
//   - Zoom con botones +/- y rueda del ratón (1× → 8×, paso 1.25).
//   - Pan con arrastre cuando el zoom > 1× (cursor grab → grabbing).
//   - Doble-click alterna 1× ↔ 2.5×.
//   - Cerrar con ESC, click fuera de la imagen, o botón ×.
//   - Reset al cambiar de imagen (key={src} en el padre lo garantiza).

import { useCallback, useEffect, useRef, useState } from "react";

const MIN_ZOOM = 1;
const MAX_ZOOM = 8;
const ZOOM_STEP = 1.25;

export interface ImageModalProps {
  src: string;
  alt: string;
  filename: string;
  sizeBytes?: number;
  open: boolean;
  onClose: () => void;
}

function fmtSize(b?: number): string {
  if (b === undefined) return "";
  if (b < 1024) return `${b} B`;
  if (b < 1024 * 1024) return `${(b / 1024).toFixed(1)} KB`;
  return `${(b / 1024 / 1024).toFixed(1)} MB`;
}

export function ImageModal({ src, alt, filename, sizeBytes, open, onClose }: ImageModalProps) {
  const [zoom, setZoom] = useState(1);
  // offsetX/offsetY: traslación en píxeles (escala CSS = 1, así son px reales).
  const [offset, setOffset] = useState({ x: 0, y: 0 });
  const dragRef = useRef<{ startX: number; startY: number; baseX: number; baseY: number } | null>(null);
  const [loaded, setLoaded] = useState(false);
  const [error, setError] = useState<string | null>(null);

  // Reset al abrir / cambiar de imagen.
  useEffect(() => {
    if (open) {
      setZoom(1);
      setOffset({ x: 0, y: 0 });
      setLoaded(false);
      setError(null);
    }
  }, [open, src]);

  // ESC cierra.
  useEffect(() => {
    if (!open) return;
    const onKey = (e: KeyboardEvent) => {
      if (e.key === "Escape") onClose();
      else if (e.key === "+" || e.key === "=") setZoom((z) => clamp(z * ZOOM_STEP));
      else if (e.key === "-") setZoom((z) => clamp(z / ZOOM_STEP));
      else if (e.key === "0") {
        setZoom(1);
        setOffset({ x: 0, y: 0 });
      }
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [open, onClose]);

  // Bloquear scroll del body mientras el modal está abierto.
  useEffect(() => {
    if (!open) return;
    const prev = document.body.style.overflow;
    document.body.style.overflow = "hidden";
    return () => {
      document.body.style.overflow = prev;
    };
  }, [open]);

  const zoomBy = useCallback((factor: number, centerX?: number, centerY?: number) => {
    setZoom((prev) => {
      const next = clamp(prev * factor);
      // Zoom-to-cursor: si el usuario usa la rueda, mantener el punto bajo
      // el cursor anclado. centerX/Y vienen en coords del stage.
      if (centerX !== undefined && centerY !== undefined) {
        const ratio = next / prev;
        setOffset((o) => ({
          x: centerX - (centerX - o.x) * ratio,
          y: centerY - (centerY - o.y) * ratio,
        }));
      }
      return next;
    });
  }, []);

  const onWheel = useCallback(
    (e: React.WheelEvent<HTMLDivElement>) => {
      e.preventDefault();
      const factor = e.deltaY < 0 ? ZOOM_STEP : 1 / ZOOM_STEP;
      const rect = e.currentTarget.getBoundingClientRect();
      zoomBy(factor, e.clientX - rect.left, e.clientY - rect.top);
    },
    [zoomBy],
  );

  const onMouseDown = useCallback((e: React.MouseEvent<HTMLDivElement>) => {
    if (zoom <= 1) return; // sin pan a 1×
    e.preventDefault();
    dragRef.current = {
      startX: e.clientX,
      startY: e.clientY,
      baseX: offset.x,
      baseY: offset.y,
    };
  }, [zoom, offset]);

  const onMouseMove = useCallback((e: React.MouseEvent<HTMLDivElement>) => {
    const drag = dragRef.current;
    if (!drag) return;
    setOffset({
      x: drag.baseX + (e.clientX - drag.startX),
      y: drag.baseY + (e.clientY - drag.startY),
    });
  }, []);

  const onMouseUp = useCallback(() => {
    dragRef.current = null;
  }, []);

  const onDoubleClick = useCallback((e: React.MouseEvent<HTMLImageElement>) => {
    e.stopPropagation();
    if (zoom > 1) {
      setZoom(1);
      setOffset({ x: 0, y: 0 });
    } else {
      const rect = e.currentTarget.parentElement!.getBoundingClientRect();
      zoomBy(2.5, e.clientX - rect.left, e.clientY - rect.top);
    }
  }, [zoom, zoomBy]);

  if (!open) return null;

  const cursor = zoom > 1 ? (dragRef.current ? "grabbing" : "grab") : "zoom-in";

  return (
    <div className="img-modal-overlay" onClick={onClose} role="dialog" aria-modal="true" aria-label={filename}>
      <header className="img-modal-header" onClick={(e) => e.stopPropagation()}>
        <span className="img-modal-title" title={filename}>
          🖼️ {filename}
        </span>
        <span className="img-modal-info">
          {fmtSize(sizeBytes)}
          {loaded && <> · {(zoom * 100).toFixed(0)}%</>}
        </span>
        <button className="img-modal-close" onClick={onClose} title="Cerrar (Esc)" aria-label="Cerrar">×</button>
      </header>

      <div
        className="img-modal-stage"
        onClick={(e) => { if (e.target === e.currentTarget) onClose(); }}
        onWheel={onWheel}
        onMouseDown={onMouseDown}
        onMouseMove={onMouseMove}
        onMouseUp={onMouseUp}
        onMouseLeave={onMouseUp}
        style={{ cursor }}
      >
        {error ? (
          <div className="img-modal-error" onClick={(e) => e.stopPropagation()}>
            No se pudo cargar la imagen: {error}
          </div>
        ) : (
          <img
            key={src}
            src={src}
            alt={alt}
            draggable={false}
            className="img-modal-img"
            style={{
              transform: `translate(${offset.x}px, ${offset.y}px) scale(${zoom})`,
              opacity: loaded ? 1 : 0,
            }}
            onLoad={() => setLoaded(true)}
            onError={() => setError("formato no soportado o inaccesible")}
            onDoubleClick={onDoubleClick}
          />
        )}
        {!loaded && !error && <div className="img-modal-loading">Cargando…</div>}
      </div>

      <footer className="img-modal-controls" onClick={(e) => e.stopPropagation()}>
        <button onClick={() => { setZoom(1); setOffset({ x: 0, y: 0 }); }} title="Restablecer (0)">⟲</button>
        <button onClick={() => zoomBy(1 / ZOOM_STEP)} title="Alejar (-)">−</button>
        <span className="img-modal-zoom-val">{(zoom * 100).toFixed(0)}%</span>
        <button onClick={() => zoomBy(ZOOM_STEP)} title="Acercar (+)">+</button>
        <a className="img-modal-download" href={src} download={filename} title="Descargar">
          ⬇
        </a>
      </footer>
    </div>
  );
}

function clamp(z: number): number {
  return Math.min(MAX_ZOOM, Math.max(MIN_ZOOM, z));
}
