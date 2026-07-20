// Lista de proyectos (paridad 1:1 con /projects/ legacy).
// Reemplaza el placeholder. Consume /api/v1/projects/.

import { useQuery } from "@tanstack/react-query";
import { api } from "@/lib/api";

interface Project {
  slug: string;
  name: string;
  path: string;
  status: string;
  github_repo: string | null;
  github_enabled: boolean;
  github_warn_no_push: boolean;
}

export function ProjectsPage() {
  const q = useQuery({
    queryKey: ["projects"],
    queryFn: () => api<Project[]>("/api/v1/projects/"),
  });
  if (q.isLoading) return <p>Cargando…</p>;
  if (q.error) return <p className="msg error">Error: {String(q.error)}</p>;
  const data = q.data ?? [];
  return (
    <div>
      <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center" }}>
        <h1>Proyectos</h1>
        <a href="/projects/new/">
          <button className="primary">+ Nuevo</button>
        </a>
      </div>
      {data.length === 0 && <p>No hay proyectos.</p>}
      <ul>
        {data.map((p: Project) => (
          <li key={p.slug}>
            <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center" }}>
              <div>
                <strong>{p.name}</strong>{" "}
                <span className="meta">({p.slug})</span>
                <br />
                <code style={{ fontSize: "0.85em" }}>{p.path}</code>
              </div>
              <div style={{ display: "flex", gap: "0.4rem", alignItems: "center" }}>
                {p.github_enabled && p.github_repo && (
                  <span className="meta">
                    <code>{p.github_repo}</code>
                  </span>
                )}
                {p.github_warn_no_push && (
                  <span className="tag warn">sin push</span>
                )}
                <a href={`/projects/${p.slug}/start/`}>
                  <button>▶ Start</button>
                </a>
              </div>
            </div>
          </li>
        ))}
      </ul>
    </div>
  );
}