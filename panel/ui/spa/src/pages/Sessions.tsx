// Lista de sesiones (UX-S.1) — tabla con filtros server-side + status tags.
// Consume /api/v1/sessions/?status=&project=&q=&limit=.

import { useMemo, useState } from "react";
import { useQuery } from "@tanstack/react-query";
import { api } from "@/lib/api";

interface Session {
  id: string;
  project: string;
  project_slug: string;
  status: string;
  started_at: string | null;
  ended_at: string | null;
  total_cost_usd: number;
  model_reported: string | null;
}

interface SessionsResponse {
  total: number;
  limit: number;
  offset: number;
  results: Session[];
}

// Paleta semántica de status — estilo tag con bg light + fg dark.
const STATUS_STYLE: Record<string, { bg: string; fg: string; label: string }> = {
  starting:           { bg: "#ddf4ff", fg: "#0550ae", label: "starting" },
  running:            { bg: "#dafbe1", fg: "#1a7f37", label: "running" },
  waiting_approval:   { bg: "#fff8c5", fg: "#9a6700", label: "esperando" },
  idle:               { bg: "#ddf4ff", fg: "#0550ae", label: "idle" },
  stopped:            { bg: "#eaeef2", fg: "#57606a", label: "stopped" },
  crashed:            { bg: "#ffebe9", fg: "#cf222e", label: "crashed" },
};

const ALL_STATUSES = Object.keys(STATUS_STYLE);

function StatusTag({ status }: { status: string }) {
  const s = STATUS_STYLE[status] ?? { bg: "#eaeef2", fg: "#57606a", label: status };
  return (
    <span
      style={{
        display: "inline-block",
        padding: "0 0.5rem",
        borderRadius: 12,
        fontSize: "0.78em",
        fontWeight: 600,
        background: s.bg,
        color: s.fg,
        letterSpacing: 0.2,
        whiteSpace: "nowrap",
      }}
    >
      {s.label}
    </span>
  );
}

function fmtTs(iso: string | null): string {
  if (!iso) return "—";
  const d = new Date(iso);
  if (Number.isNaN(d.getTime())) return iso;
  // Formato corto "dd HH:MM" — útil en pantalla ancha de 1080p.
  const dd = String(d.getDate()).padStart(2, "0");
  const hh = String(d.getHours()).padStart(2, "0");
  const mm = String(d.getMinutes()).padStart(2, "0");
  return `${dd} ${hh}:${mm}`;
}

export function SessionsPage() {
  // Filtros UI → query string
  const [filterStatus, setFilterStatus] = useState<string[]>([]);
  const [filterProject, setFilterProject] = useState("");
  const [filterText, setFilterText] = useState("");

  const queryString = useMemo(() => {
    const sp = new URLSearchParams();
    if (filterStatus.length) sp.set("status", filterStatus.join(","));
    if (filterProject.trim()) sp.set("project", filterProject.trim());
    if (filterText.trim()) sp.set("q", filterText.trim());
    sp.set("limit", "500");
    const q = sp.toString();
    return q ? `?${q}` : "";
  }, [filterStatus, filterProject, filterText]);

  const q = useQuery({
    queryKey: ["sessions", queryString],
    queryFn: () => api<SessionsResponse>(`/api/v1/sessions/${queryString}`),
    refetchInterval: 10_000,
  });

  const toggleStatus = (s: string) =>
    setFilterStatus((prev) =>
      prev.includes(s) ? prev.filter((x) => x !== s) : [...prev, s],
    );

  if (q.isLoading) return <p>Cargando…</p>;
  if (q.error) return <p className="msg error">Error: {String(q.error)}</p>;
  const resp = q.data;
  const results = resp?.results ?? [];
  const total = resp?.total ?? 0;

  // Conteos por status para los chips de filtro
  const counts: Record<string, number> = {};
  for (const s of results) counts[s.status] = (counts[s.status] || 0) + 1;

  return (
    <div>
      <div style={{ display: "flex", alignItems: "center", gap: "1rem" }}>
        <h1 style={{ margin: 0 }}>Sesiones</h1>
        <span className="meta">
          {total} resultado{total === 1 ? "" : "s"}
        </span>
      </div>

      {/* Filtros */}
      <div className="filters-bar">
        <input
          type="search"
          placeholder="Buscar por slug o prefijo UUID…"
          value={filterText}
          onChange={(e) => setFilterText(e.target.value)}
          style={{ flex: 1, minWidth: 200 }}
        />
        <input
          type="text"
          placeholder="Proyecto (slug)…"
          value={filterProject}
          onChange={(e) => setFilterProject(e.target.value)}
          style={{ width: 220 }}
        />
        <button
          onClick={() => {
            setFilterStatus([]);
            setFilterProject("");
            setFilterText("");
          }}
          disabled={
            filterStatus.length === 0 &&
            !filterProject.trim() &&
            !filterText.trim()
          }
        >
          Limpiar filtros
        </button>
      </div>

      <div className="status-chips">
        {ALL_STATUSES.map((s) => {
          const active = filterStatus.includes(s);
          const cnt = counts[s] || 0;
          const sty = STATUS_STYLE[s];
          return (
            <button
              key={s}
              type="button"
              onClick={() => toggleStatus(s)}
              className={`status-chip ${active ? "active" : ""}`}
              style={{
                background: active ? sty.bg : "var(--bg)",
                color: active ? sty.fg : "var(--muted)",
                borderColor: active ? sty.fg : "var(--border)",
              }}
            >
              <span>{sty.label}</span>
              {cnt > 0 && <span className="count">{cnt}</span>}
            </button>
          );
        })}
      </div>

      {results.length === 0 ? (
        <p className="meta" style={{ marginTop: "1rem" }}>
          {total === 0 ? "No hay sesiones." : "Ningún resultado con los filtros actuales."}
        </p>
      ) : (
        <table className="sessions-table">
          <thead>
            <tr>
              <th>Proyecto</th>
              <th>Estado</th>
              <th>UUID</th>
              <th>Inicio</th>
              <th>Fin</th>
              <th>Modelo</th>
              <th style={{ textAlign: "right" }}>Costo</th>
            </tr>
          </thead>
          <tbody>
            {results.map((s) => (
              <tr key={s.id}>
                <td>
                  <a href={`/sessions/${s.id}`}>
                    <strong>{s.project_slug}</strong>
                  </a>
                </td>
                <td>
                  <StatusTag status={s.status} />
                </td>
                <td>
                  <a
                    href={`/sessions/${s.id}`}
                    style={{ fontFamily: "ui-monospace, monospace", fontSize: "0.85em" }}
                  >
                    {s.id.slice(0, 8)}
                  </a>
                </td>
                <td className="meta">{fmtTs(s.started_at)}</td>
                <td className="meta">{fmtTs(s.ended_at)}</td>
                <td className="meta">{s.model_reported ?? "—"}</td>
                <td style={{ textAlign: "right", fontFamily: "ui-monospace, monospace" }}>
                  ${s.total_cost_usd.toFixed(4)}
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      )}
    </div>
  );
}
