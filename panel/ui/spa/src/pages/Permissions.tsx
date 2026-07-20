// Cola global de aprobaciones (paridad 1:1 con /permisos/ legacy).
// Lista PermissionRequest pending filtradas por sesión viva (D11).

import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { api } from "@/lib/api";

interface Permission {
  id: string;
  session: string;
  tool: string;
  input_preview: string;
  status: string;
  resolved_by: string | null;
  expires_at: string;
  session_status: string;
  project_slug: string;
}

export function PermissionsPage() {
  const qc = useQueryClient();
  const q = useQuery({
    queryKey: ["permissions"],
    queryFn: () => api<Permission[]>("/api/v1/permissions/"),
    refetchInterval: 3000, // refresca cada 3s
  });
  const resolve = useMutation({
    mutationFn: ({ id, answer }: { id: string; answer: "allow" | "deny" }) =>
      api(`/api/v1/permissions/${id}/resolve/`, { method: "POST", body: { answer } }),
    onSuccess: () => qc.invalidateQueries({ queryKey: ["permissions"] }),
  });

  if (q.isLoading) return <p>Cargando…</p>;
  if (q.error) return <p className="msg error">Error: {String(q.error)}</p>;
  const data = q.data ?? [];

  return (
    <div>
      <h1>Aprobaciones pendientes</h1>
      {data.length === 0 && <p>Ninguna aprobación pendiente.</p>}
      <ul>
        {data.map((p) => (
          <li key={p.id}>
            <div style={{ display: "flex", justifyContent: "space-between", alignItems: "flex-start" }}>
              <div style={{ flex: 1 }}>
                <div>
                  <strong>{p.tool}</strong>{" "}
                  <span className="meta">
                    · {p.project_slug} / sesión {p.session.slice(0, 8)} ({p.session_status})
                  </span>
                </div>
                <pre className="unboxed" style={{ marginTop: "0.3rem", maxHeight: 100 }}>
                  {p.input_preview}
                </pre>
                <div className="meta">expira: {new Date(p.expires_at).toLocaleString()}</div>
              </div>
              <div style={{ display: "flex", gap: "0.4rem", marginLeft: "0.5rem" }}>
                <button
                  className="primary"
                  disabled={resolve.isPending}
                  onClick={() => resolve.mutate({ id: p.id, answer: "allow" })}
                >
                  Permitir
                </button>
                <button
                  className="danger"
                  disabled={resolve.isPending}
                  onClick={() => resolve.mutate({ id: p.id, answer: "deny" })}
                >
                  Denegar
                </button>
              </div>
            </div>
          </li>
        ))}
      </ul>
    </div>
  );
}