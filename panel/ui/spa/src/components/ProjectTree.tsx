// Componentes del panel lateral: árbol de archivos, visor, diff.
// FASE C.5 — consumen /api/v1/projects/<slug>/{tree,file,diff}.
// Imágenes se sirven vía /raw/ y se abren en ImageModal con zoom.
// Archivos de texto se abren en TextFileModal (copy + download).

import { useEffect, useState } from "react";
import { useQuery } from "@tanstack/react-query";
import { api } from "@/lib/api";
import { ImageModal } from "./ImageModal";
import { TextFileModal } from "./TextFileModal";

// Allowlist de extensiones que se renderizan como imagen. Coincide con
// RAW_IMAGE_EXTS del backend; si se desincroniza, el backend responderá 403
// y el modal mostrará un error limpio.
const IMAGE_EXTS = new Set([
  "png", "jpg", "jpeg", "gif", "webp", "svg", "bmp", "ico", "avif", "heic", "heif",
]);
function isImage(name: string): boolean {
  const i = name.lastIndexOf(".");
  return i >= 0 && IMAGE_EXTS.has(name.slice(i + 1).toLowerCase());
}

interface TreeEntry {
  name: string;
  is_dir: boolean;
  size: number;
}

interface Tree {
  path: string;
  entries: TreeEntry[];
}

interface File {
  path: string;
  size: number;
  is_binary: boolean;
  truncated: boolean;
  content: string | null;
}

function fmtSize(b: number): string {
  if (b < 1024) return `${b} B`;
  if (b < 1024 * 1024) return `${(b / 1024).toFixed(1)} KB`;
  return `${(b / 1024 / 1024).toFixed(1)} MB`;
}

export function ProjectTree({ slug }: { slug: string }) {
  const [cwd, setCwd] = useState(".");
  const [selected, setSelected] = useState<string | null>(null);
  // Imagen actualmente abierta en el modal.
  const [imageModal, setImageModal] = useState<{ path: string; size: number } | null>(null);
  // Texto abierto en el modal. Se setea cuando llega la respuesta del
  // /file/ y resulta ser no-binaria. Si es binaria, queda en null y se
  // muestra el placeholder inline de siempre.
  const [textModal, setTextModal] = useState<{ path: string; size: number; content: string; truncated: boolean } | null>(null);

  const tree = useQuery({
    queryKey: ["tree", slug, cwd],
    queryFn: () => api<Tree>(`/api/v1/projects/${slug}/tree/?path=${encodeURIComponent(cwd)}`),
    enabled: !!slug,
  });

  const file = useQuery({
    queryKey: ["file", slug, selected],
    queryFn: () => api<File>(`/api/v1/projects/${slug}/file/?path=${encodeURIComponent(selected!)}`),
    enabled: !!selected && !imageModal,
  });

  // Cuando llega la respuesta de /file/: si es texto, abrimos el modal.
  // Si es binario, no abrimos modal — el render de abajo muestra el placeholder.
  useEffect(() => {
    if (!file.data || imageModal) return;
    if (file.data.is_binary || file.data.content === null) return;
    setTextModal({
      path: selected!,
      size: file.data.size,
      content: file.data.content,
      truncated: file.data.truncated,
    });
  }, [file.data, imageModal, selected]);

  const onPickEntry = (e: TreeEntry) => {
    if (e.is_dir) {
      const next = cwd === "." ? e.name : `${cwd}/${e.name}`;
      setCwd(next);
      setSelected(null);
      setImageModal(null);
      setTextModal(null);
    } else {
      const next = cwd === "." ? e.name : `${cwd}/${e.name}`;
      if (isImage(e.name)) {
        // Imágenes: NO cargar /file/ (que solo devuelve metadata para
        // binarios); abrir directamente el modal con la URL al endpoint raw.
        setTextModal(null);
        setSelected(next);
        setImageModal({ path: next, size: e.size });
      } else {
        // Texto o binario: dejamos que el useQuery traiga /file/ y el
        // useEffect abra el TextFileModal si corresponde.
        setImageModal(null);
        setTextModal(null);
        setSelected(next);
      }
    }
  };

  const goUp = () => {
    if (cwd === ".") return;
    const parts = cwd.split("/");
    parts.pop();
    setCwd(parts.join("/") || ".");
    setSelected(null);
    setImageModal(null);
    setTextModal(null);
  };

  return (
    <div>
      <div style={{ display: "flex", gap: "0.4rem", alignItems: "center" }}>
        <button onClick={goUp} disabled={cwd === "."} title="Subir">↑</button>
        <code style={{ fontSize: "0.85em", flex: 1, overflow: "hidden", textOverflow: "ellipsis" }}>
          {cwd === "." ? "/" : `/${cwd}`}
        </code>
      </div>

      {tree.isLoading && <p className="meta">Cargando árbol…</p>}
      {tree.error && <p className="msg error">Error: {String(tree.error)}</p>}

      {tree.data && (
        <ul style={{ marginTop: "0.3rem" }}>
          {tree.data.entries.length === 0 && <li className="meta">(vacío)</li>}
          {tree.data.entries.map((e) => (
            <li
              key={e.name}
              style={{ cursor: "pointer", padding: "0.2rem 0.4rem" }}
              onClick={() => onPickEntry(e)}
              title={isImage(e.name) ? `${e.name} — click para ver` : undefined}
            >
              <span style={{ fontFamily: "ui-monospace, monospace", fontSize: "0.9em" }}>
                {e.is_dir ? "📁" : isImage(e.name) ? "🖼️" : "📄"} {e.name}
              </span>
              {!e.is_dir && <span className="meta" style={{ marginLeft: "0.4rem" }}>{fmtSize(e.size)}</span>}
            </li>
          ))}
        </ul>
      )}

      {selected && !imageModal && !textModal && (
        <div style={{ marginTop: "0.5rem" }}>
          <div className="meta">📄 {selected}</div>
          {file.isLoading && <p className="meta">Cargando…</p>}
          {file.error && <p className="msg error">Error: {String(file.error)}</p>}
          {file.data?.is_binary && (
            <p className="meta">binario ({fmtSize(file.data.size)})</p>
          )}
        </div>
      )}

      {imageModal && (
        <ImageModal
          open={true}
          src={`/api/v1/projects/${slug}/raw/?path=${encodeURIComponent(imageModal.path)}`}
          alt={imageModal.path}
          filename={imageModal.path.split("/").pop() || imageModal.path}
          sizeBytes={imageModal.size}
          onClose={() => setImageModal(null)}
        />
      )}

      {textModal && (
        <TextFileModal
          open={true}
          content={textModal.content}
          filename={textModal.path.split("/").pop() || textModal.path}
          sizeBytes={textModal.size}
          truncated={textModal.truncated}
          downloadUrl={`/api/v1/projects/${slug}/raw/?path=${encodeURIComponent(textModal.path)}`}
          onClose={() => setTextModal(null)}
        />
      )}
    </div>
  );
}

interface DiffFiles {
  files: Array<{
    path: string;
    status: string;       // M, A, D, R, ??
    additions: number;
    deletions: number;
    is_binary: boolean;
  }>;
  not_a_repo?: boolean;
  error?: string;
}

interface DiffFileContent {
  path: string;
  diff?: string;
  not_a_repo?: boolean;
  error?: string;
}

const STATUS_LABEL: Record<string, string> = {
  M: "modificado",
  A: "añadido",
  D: "borrado",
  R: "renombrado",
  "??": "untracked",
};

function StatusBadge({ status }: { status: string }) {
  const label = STATUS_LABEL[status] ?? status;
  const cls = status === "A" ? "tag ok" : status === "D" ? "tag err" : status === "??" ? "tag" : "tag warn";
  return <span className={cls}>{label}</span>;
}

// DiffLine retirado en SP16: el diff se ve ahora en TextFileModal (mejor
// con zoom/pan/copy/descarga). Si vuelves a necesitar colorear +/- por línea,
// busca en git history (commit pre-SP16).

export function ProjectDiff({ slug }: { slug: string }) {
  const filesQ = useQuery({
    queryKey: ["diff-files", slug],
    queryFn: () => api<DiffFiles>(`/api/v1/projects/${slug}/diff/files/`),
    enabled: !!slug,
    refetchInterval: 5000,
  });
  // SP16: cada archivo se ve en modal (igual que el file viewer). `path` =
  // archivo cuyo diff está abierto; `body` = contenido ya cargado (mientras
  // carga, el modal muestra "Cargando…").
  const [diffModal, setDiffModal] = useState<string | null>(null);

  if (filesQ.isLoading) return <p className="meta">Cargando diff…</p>;
  if (filesQ.error) return <p className="msg error">Error: {String(filesQ.error)}</p>;
  const data = filesQ.data;
  if (!data) return null;
  if (data.not_a_repo) {
    return <p className="meta">El proyecto no es un repo git — sin diff.</p>;
  }
  const files = data.files ?? [];
  const totalAdds = files.reduce((s, f) => s + (f.additions || 0), 0);
  const totalDels = files.reduce((s, f) => s + (f.deletions || 0), 0);
  const dirty = files.length > 0;

  return (
    <div>
      <div className="diff-header">
        <span>
          {dirty
            ? `${files.length} archivo${files.length === 1 ? "" : "s"} con cambios`
            : "Working tree limpio"}
        </span>
        {dirty && (
          <span style={{ marginLeft: "auto", display: "flex", gap: "0.4rem", fontFamily: "ui-monospace, monospace" }}>
            <span style={{ color: "var(--ok-fg)", fontWeight: 600 }}>+{totalAdds}</span>
            <span style={{ color: "var(--err-fg)", fontWeight: 600 }}>−{totalDels}</span>
          </span>
        )}
      </div>
      {files.length === 0 && <p className="meta">No hay cambios pendientes.</p>}
      <ul className="diff-tree">
        {files.map((f) => (
          <li key={f.path} className="diff-item">
            <div className="diff-row" aria-disabled={f.is_binary}>
              <span className="diff-chevron" aria-hidden>
                {f.is_binary ? "" : "▸"}
              </span>
              <span className="diff-filename">
                <StatusBadge status={f.status} />{" "}
                <code>{f.path}</code>
              </span>
              {!f.is_binary && (
                <span className="diff-counts">
                  <span style={{ color: "var(--ok-fg)" }}>+{f.additions}</span>
                  <span style={{ color: "var(--err-fg)", marginLeft: "0.4rem" }}>−{f.deletions}</span>
                </span>
              )}
              {f.is_binary ? (
                <span className="diff-counts meta">binario</span>
              ) : (
                <button
                  type="button"
                  className="diff-view-btn"
                  onClick={() => setDiffModal(f.path)}
                >
                  Ver diff
                </button>
              )}
            </div>
          </li>
        ))}
      </ul>
      {diffModal && (
        <DiffViewerModal
          slug={slug}
          path={diffModal}
          onClose={() => setDiffModal(null)}
        />
      )}
    </div>
  );
}

function DiffViewerModal({
  slug, path, onClose,
}: { slug: string; path: string; onClose: () => void }) {
  const q = useQuery({
    queryKey: ["diff-file", slug, path],
    queryFn: () => api<DiffFileContent>(
      `/api/v1/projects/${slug}/diff/file/?path=${encodeURIComponent(path)}`,
    ),
  });

  const diff = q.data?.diff ?? "";
  const isError = !!q.error || (q.data && "error" in q.data && q.data.error);
  const filename = `${path}.diff`;
  // Blob URL para que Descargar funcione sin endpoint raw. Si reusar el
  // mismo path abre el modal varias veces, se re-genera con el contenido
  // actual. URL.revokeObjectURL lo hace el TextFileModal al cerrarse.
  const downloadUrl = useBlobUrl(diff, "text/plain;charset=utf-8");

  if (q.isLoading) {
    return (
      <TextFileModal
        open
        content="(cargando…)"
        filename={filename}
        onClose={onClose}
      />
    );
  }
  if (isError) {
    return (
      <TextFileModal
        open
        content={`Error al obtener el diff: ${String(q.error ?? q.data?.error ?? "desconocido")}`}
        filename={filename}
        onClose={onClose}
      />
    );
  }
  return (
    <TextFileModal
      open
      content={diff}
      filename={filename}
      downloadUrl={downloadUrl}
      onClose={onClose}
    />
  );
}

// Hook local: crea/revoca una Blob URL para un texto. La renueva si el
// contenido o el tipo cambia. El TextFileModal la usa para "Descargar".
function useBlobUrl(text: string, mime: string): string {
  const [url, setUrl] = useState<string>("");
  useEffect(() => {
    if (typeof URL === "undefined" || typeof Blob === "undefined") return;
    const blob = new Blob([text], { type: mime });
    const u = URL.createObjectURL(blob);
    setUrl(u);
    return () => URL.revokeObjectURL(u);
  }, [text, mime]);
  return url;
}