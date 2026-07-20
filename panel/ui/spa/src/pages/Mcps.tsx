// Lista de MCP servers (paridad 1:1 con /mcps/ legacy).
// Reemplaza el placeholder. Consume /api/v1/mcps/.

import { useQuery } from "@tanstack/react-query";
import { api } from "@/lib/api";

interface Mcp {
  id: number;
  name: string;
  scope: "global" | "project";
  project: string | null;
  transport: "stdio" | "http";
  config: Record<string, unknown>;
  enabled: boolean;
  updated_at: string;
}

export function McpsPage() {
  const q = useQuery({
    queryKey: ["mcps"],
    queryFn: () => api<Mcp[]>("/api/v1/mcps/"),
  });
  if (q.isLoading) return <p>Cargando…</p>;
  if (q.error) return <p className="msg error">Error: {String(q.error)}</p>;
  const data = q.data ?? [];
  return (
    <div>
      <h1>MCPs</h1>
      {data.length === 0 && <p>No hay MCPs configurados.</p>}
      <ul>
        {data.map((m: Mcp) => (
          <li key={m.id}>
            <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center" }}>
              <div>
                <strong>{m.name}</strong>{" "}
                <span className="meta">[{m.scope}{m.project ? `: ${m.project}` : ""}]</span>
                <br />
                <code style={{ fontSize: "0.85em" }}>{m.transport}</code>
                {Object.keys(m.config).length > 0 && (
                  <>
                    <br />
                    <code style={{ fontSize: "0.8em" }}>
                      {JSON.stringify(m.config)}
                    </code>
                  </>
                )}
              </div>
              <span className="tag" style={{
                background: m.enabled ? "#dfd" : "#eee",
                color: m.enabled ? "#116329" : "#666",
              }}>
                {m.enabled ? "enabled" : "disabled"}
              </span>
            </div>
          </li>
        ))}
      </ul>
    </div>
  );
}