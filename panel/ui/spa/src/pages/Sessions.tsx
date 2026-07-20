// Lista de sesiones — consume /api/v1/sessions/.

import { useQuery } from "@tanstack/react-query";
import { api } from "@/lib/api";

interface Session {
  id: string;
  project: string;
  status: string;
  started_at: string | null;
  ended_at: string | null;
  total_cost_usd: number;
}

export function SessionsPage() {
  const q = useQuery({
    queryKey: ["sessions"],
    queryFn: () => api<Session[]>("/api/v1/sessions/"),
  });
  if (q.isLoading) return <p>Cargando…</p>;
  if (q.error) return <p>Error: {String(q.error)}</p>;
  const data = q.data ?? [];
  return (
    <div>
      <h1>Sesiones</h1>
      {data.length === 0 && <p>No hay sesiones.</p>}
      <ul>
        {data.map((s: Session) => (
          <li key={s.id}>
            <a href={`/sessions/${s.id}`}>
              {s.project} · {s.status} · ${s.total_cost_usd.toFixed(4)}
            </a>{" "}
            <span style={{ opacity: 0.6, fontSize: "0.85em" }}>{s.id.slice(0, 8)}</span>
          </li>
        ))}
      </ul>
    </div>
  );
}