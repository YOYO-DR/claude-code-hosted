// Componentes del panel lateral: árbol de archivos, visor, diff.
// FASE C.5 — consumen /api/v1/projects/<slug>/{tree,file,diff}.

import { useState } from "react";
import { useQuery } from "@tanstack/react-query";
import { api } from "@/lib/api";

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

  const tree = useQuery({
    queryKey: ["tree", slug, cwd],
    queryFn: () => api<Tree>(`/api/v1/projects/${slug}/tree/?path=${encodeURIComponent(cwd)}`),
    enabled: !!slug,
  });

  const file = useQuery({
    queryKey: ["file", slug, selected],
    queryFn: () => api<File>(`/api/v1/projects/${slug}/file/?path=${encodeURIComponent(selected!)}`),
    enabled: !!selected,
  });

  const onPickEntry = (e: TreeEntry) => {
    if (e.is_dir) {
      // ruta absoluta o relativa: combinamos con cwd actual.
      const next = cwd === "." ? e.name : `${cwd}/${e.name}`;
      setCwd(next);
      setSelected(null);
    } else {
      const next = cwd === "." ? e.name : `${cwd}/${e.name}`;
      setSelected(next);
    }
  };

  const goUp = () => {
    if (cwd === ".") return;
    const parts = cwd.split("/");
    parts.pop();
    setCwd(parts.join("/") || ".");
    setSelected(null);
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
            >
              <span style={{ fontFamily: "ui-monospace, monospace", fontSize: "0.9em" }}>
                {e.is_dir ? "📁" : "📄"} {e.name}
              </span>
              {!e.is_dir && <span className="meta" style={{ marginLeft: "0.4rem" }}>{fmtSize(e.size)}</span>}
            </li>
          ))}
        </ul>
      )}

      {selected && (
        <div style={{ marginTop: "0.5rem" }}>
          <div className="meta">📄 {selected}</div>
          {file.isLoading && <p className="meta">Cargando…</p>}
          {file.error && <p className="msg error">Error: {String(file.error)}</p>}
          {file.data?.is_binary && (
            <p className="meta">binario ({fmtSize(file.data.size)})</p>
          )}
          {file.data && !file.data.is_binary && (
            <pre className="unboxed" style={{ maxHeight: 240, overflow: "auto" }}>
              {file.data.content}
              {file.data.truncated && <div className="meta">(truncado)</div>}
            </pre>
          )}
        </div>
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

function DiffLine({ line }: { line: string }) {
  // Las primeras 2 columnas de un diff son "indicador + nº de línea" (ej "+
  // 1"), las saltamos para alinear visualmente con el resto del código.
  const isAdd = line.startsWith("+") && !line.startsWith("+++");
  const isDel = line.startsWith("-") && !line.startsWith("---");
  const text = line.length > 0 ? line.slice(1) : line;
  return (
    <div className={isAdd ? "diff-line add" : isDel ? "diff-line del" : "diff-line"}>
      <span className="diff-gutter">{isAdd ? "+" : isDel ? "-" : " "}</span>
      <span className="diff-text">{text || " "}</span>
    </div>
  );
}

export function ProjectDiff({ slug }: { slug: string }) {
  const filesQ = useQuery({
    queryKey: ["diff-files", slug],
    queryFn: () => api<DiffFiles>(`/api/v1/projects/${slug}/diff/files/`),
    enabled: !!slug,
    refetchInterval: 5000,
  });
  const [expanded, setExpanded] = useState<Set<string>>(new Set());

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

  const toggle = (path: string) => {
    setExpanded((prev) => {
      const next = new Set(prev);
      if (next.has(path)) next.delete(path);
      else next.add(path);
      return next;
    });
  };

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
            <span style={{ color: "#1a7f37", fontWeight: 600 }}>+{totalAdds}</span>
            <span style={{ color: "#cf222e", fontWeight: 600 }}>−{totalDels}</span>
          </span>
        )}
      </div>
      {files.length === 0 && <p className="meta">No hay cambios pendientes.</p>}
      <ul className="diff-tree">
        {files.map((f) => (
          <li key={f.path} className="diff-item">
            <button
              type="button"
              className="diff-row"
              onClick={() => !f.is_binary && toggle(f.path)}
              aria-expanded={expanded.has(f.path)}
              disabled={f.is_binary}
            >
              <span className="diff-chevron" aria-hidden>
                {f.is_binary ? "" : expanded.has(f.path) ? "▾" : "▸"}
              </span>
              <span className="diff-filename">
                <StatusBadge status={f.status} />{" "}
                <code>{f.path}</code>
              </span>
              {!f.is_binary && (
                <span className="diff-counts">
                  <span style={{ color: "#1a7f37" }}>+{f.additions}</span>
                  <span style={{ color: "#cf222e", marginLeft: "0.4rem" }}>−{f.deletions}</span>
                </span>
              )}
              {f.is_binary && (
                <span className="diff-counts meta">binario</span>
              )}
            </button>
            {expanded.has(f.path) && !f.is_binary && (
              <DiffBody slug={slug} path={f.path} />
            )}
          </li>
        ))}
      </ul>
    </div>
  );
}

function DiffBody({ slug, path }: { slug: string; path: string }) {
  const q = useQuery({
    queryKey: ["diff-file", slug, path],
    queryFn: () => api<DiffFileContent>(
      `/api/v1/projects/${slug}/diff/file/?path=${encodeURIComponent(path)}`,
    ),
  });
  if (q.isLoading) return <p className="meta" style={{ padding: "0.4rem 1.5rem" }}>Cargando diff…</p>;
  if (q.error || !q.data || q.data.error) {
    return <p className="msg error" style={{ padding: "0.4rem 1.5rem" }}>Error: {String(q.error ?? q.data?.error)}</p>;
  }
  const lines = (q.data.diff || "").split("\n");
  // Filtramos los headers de archivo "diff --git" / "index" / "---" / "+++" si
  // están al inicio (visual redundante con la fila clickeable). Conservamos las
  // líneas "@@ …" como separadores hunks.
  return (
    <pre className="diff-body unboxed">
      {lines.map((ln, i) => {
        if (ln.startsWith("diff --git") || ln.startsWith("index ")) return null;
        if (ln === "---" || ln === "+++") return null;
        if (ln.startsWith("@@")) {
          return <div key={i} className="diff-hunk">{ln}</div>;
        }
        return <DiffLine key={i} line={ln} />;
      })}
    </pre>
  );
}