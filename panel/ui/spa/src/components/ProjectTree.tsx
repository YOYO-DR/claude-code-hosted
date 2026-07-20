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

interface Diff {
  path: string | null;
  diff: string;
  dirty: boolean;
  not_a_repo?: boolean;
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

export function ProjectDiff({ slug }: { slug: string }) {
  const q = useQuery({
    queryKey: ["diff", slug],
    queryFn: () => api<Diff>(`/api/v1/projects/${slug}/diff/`),
    enabled: !!slug,
    refetchInterval: 5000, // refresca cada 5s mientras el agente trabaja
  });
  if (q.isLoading) return <p className="meta">Cargando diff…</p>;
  if (q.error) return <p className="msg error">Error: {String(q.error)}</p>;
  const d = q.data;
  if (!d) return null;
  if (d.not_a_repo) {
    return <p className="meta">El proyecto no es un repo git — sin diff.</p>;
  }
  return (
    <div>
      <div className="meta">
        {d.dirty ? "🟠 Cambios sin commit" : "✓ Working tree limpio"}
      </div>
      {d.diff && (
        <pre className="unboxed" style={{ marginTop: "0.3rem", maxHeight: 320, overflow: "auto" }}>
          {d.diff}
        </pre>
      )}
      {!d.diff && d.dirty === false && (
        <p className="meta">No hay cambios pendientes.</p>
      )}
    </div>
  );
}