// Lista de MCP servers (UX-T.3) — tabla con CRUD + modal (UI.1).

import { useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { api } from "@/lib/api";
import { Modal } from "@/components/Modal";

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
  const qc = useQueryClient();
  const q = useQuery({
    queryKey: ["mcps"],
    queryFn: () => api<Mcp[]>("/api/v1/mcps/"),
  });
  const [creating, setCreating] = useState(false);
  const [editing, setEditing] = useState<Mcp | null>(null);
  const [deleting, setDeleting] = useState<Mcp | null>(null);
  const [toggling, setToggling] = useState<Mcp | null>(null);

  const delMut = useMutation({
    mutationFn: (id: number) =>
      api(`/api/v1/mcps/${id}/delete/`, { method: "DELETE" }),
    onSuccess: () => { setDeleting(null); qc.invalidateQueries({ queryKey: ["mcps"] }); },
  });

  const toggleMut = useMutation({
    mutationFn: ({ id, enabled }: { id: number; enabled: boolean }) =>
      api(`/api/v1/mcps/${id}/update/`, { method: "PATCH", body: { enabled } }),
    onSuccess: () => { setToggling(null); qc.invalidateQueries({ queryKey: ["mcps"] }); },
  });

  if (q.isLoading) return <p>Cargando…</p>;
  if (q.error) return <p className="msg error">Error: {String(q.error)}</p>;
  const data = q.data ?? [];

  return (
    <div>
      <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center" }}>
        <h1>MCPs</h1>
        <button className="primary" onClick={() => setCreating(true)}>+ Nuevo</button>
      </div>

      {creating && (
        <McpFormModal
          mode="create"
          onSaved={() => { setCreating(false); qc.invalidateQueries({ queryKey: ["mcps"] }); }}
          onCancel={() => setCreating(false)}
        />
      )}
      {editing && (
        <McpFormModal
          mode="edit"
          mcp={editing}
          onSaved={() => { setEditing(null); qc.invalidateQueries({ queryKey: ["mcps"] }); }}
          onCancel={() => setEditing(null)}
        />
      )}
      {deleting && (
        <Modal
          open
          title={`Eliminar MCP "${deleting.name}"?`}
          variant="confirm"
          confirmLabel="Eliminar"
          danger
          busy={delMut.isPending}
          onConfirm={() => delMut.mutate(deleting.id)}
          onCancel={() => setDeleting(null)}
        >
          <p>
            El MCP se deshabilita y se regenera el <code>.mcp.json</code> del
            workspace. Para borrar la fila completamente, usa{" "}
            <code>?hard=1</code> en el backend.
          </p>
          {delMut.error && <div className="modal-error">{String(delMut.error)}</div>}
        </Modal>
      )}

      {data.length === 0 && <p>No hay MCPs configurados.</p>}
      {data.length > 0 && (
        <table className="sessions-table">
          <thead>
            <tr>
              <th>Nombre</th>
              <th>Scope</th>
              <th>Transport</th>
              <th>Project</th>
              <th>Estado</th>
              <th>Config</th>
              <th>Acciones</th>
            </tr>
          </thead>
          <tbody>
            {data.map((m) => (
              <tr key={m.id}>
                <td><strong>{m.name}</strong></td>
                <td>{m.scope}</td>
                <td><code>{m.transport}</code></td>
                <td><code>{m.project ?? "—"}</code></td>
                <td>
                  <span
                    style={{
                      display: "inline-block",
                      padding: "0 0.5rem",
                      borderRadius: 12,
                      fontSize: "0.78em",
                      fontWeight: 600,
                      background: m.enabled ? "#dafbe1" : "#eaeef2",
                      color: m.enabled ? "#1a7f37" : "#57606a",
                    }}
                  >
                    {m.enabled ? "enabled" : "disabled"}
                  </span>
                </td>
                <td>
                  <code style={{ fontSize: "0.8em", color: "var(--muted)" }}>
                    {Object.keys(m.config).length > 0 ? JSON.stringify(m.config) : "—"}
                  </code>
                </td>
                <td style={{ display: "flex", gap: "0.3rem", flexWrap: "nowrap" }}>
                  <button onClick={() => setToggling(m)}>
                    {m.enabled ? "Deshabilitar" : "Habilitar"}
                  </button>
                  <button onClick={() => setEditing(m)}>Editar</button>
                  <button className="danger" onClick={() => setDeleting(m)}>
                    Eliminar
                  </button>
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      )}

      {toggling && (
        <Modal
          open
          title={`${toggling.enabled ? "Deshabilitar" : "Habilitar"} "${toggling.name}"?`}
          variant="confirm"
          confirmLabel={toggling.enabled ? "Deshabilitar" : "Habilitar"}
          busy={toggleMut.isPending}
          onConfirm={() => toggleMut.mutate({ id: toggling.id, enabled: !toggling.enabled })}
          onCancel={() => setToggling(null)}
        >
          <p>
            El cambio regenera el <code>.mcp.json</code> con el flag{" "}
            <code>enabled</code> actualizado.
          </p>
        </Modal>
      )}
    </div>
  );
}

function McpFormModal({
  mode,
  mcp,
  onSaved,
  onCancel,
}: {
  mode: "create" | "edit";
  mcp?: Mcp;
  onSaved: () => void;
  onCancel: () => void;
}) {
  const projectsQ = useQuery({
    queryKey: ["projects"],
    queryFn: () => api<Array<{ slug: string; name: string }>>("/api/v1/projects/"),
  });
  const [name, setName] = useState(mcp?.name ?? "");
  const [scope, setScope] = useState<"global" | "project">(mcp?.scope ?? "global");
  const [project, setProject] = useState(mcp?.project ?? "");
  const [transport, setTransport] = useState<"stdio" | "http">(mcp?.transport ?? "stdio");
  const [configText, setConfigText] = useState(
    mcp ? JSON.stringify(mcp.config, null, 2) : "{}",
  );
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState<string | null>(null);

  const saveMut = useMutation({
    mutationFn: (body: Record<string, unknown>) =>
      mode === "create"
        ? api("/api/v1/mcps/create/", { method: "POST", body })
        : api(`/api/v1/mcps/${mcp!.id}/update/`, { method: "PATCH", body }),
    onSuccess: () => { setBusy(false); onSaved(); },
    onError: (e: unknown) => {
      setBusy(false);
      const m = (() => {
        try { return JSON.parse(String(e)).error ?? String(e); }
        catch { return String(e); }
      })();
      setErr(m);
    },
  });

  const submit = () => {
    setErr(null);
    let cfg: Record<string, unknown>;
    try {
      cfg = JSON.parse(configText || "{}");
    } catch {
      return setErr("config debe ser JSON válido");
    }
    const body: Record<string, unknown> = {
      name: name.trim(),
      scope,
      transport,
      config: cfg,
      enabled: true,
    };
    if (scope === "project") body.project = project.trim() || null;
    else body.project = null;
    setBusy(true);
    saveMut.mutate(body);
  };

  return (
    <Modal
      open
      variant="custom"
      title={mode === "create" ? "Nuevo MCP" : `Editar MCP "${mcp?.name}"`}
      onCancel={onCancel}
    >
      <label>Nombre *</label>
      <input value={name} onChange={(e) => setName(e.target.value)} required maxLength={100} />
      <label>Scope</label>
      <select value={scope} onChange={(e) => setScope(e.target.value as typeof scope)}>
        <option value="global">global</option>
        <option value="project">project</option>
      </select>
      {scope === "project" && (
        <>
          <label>Proyecto (slug)</label>
          <select value={project} onChange={(e) => setProject(e.target.value)}>
            <option value="">— elegir proyecto —</option>
            {(projectsQ.data ?? []).map((p) => (
              <option key={p.slug} value={p.slug}>{p.slug}</option>
            ))}
          </select>
        </>
      )}
      <label>Transport</label>
      <select value={transport} onChange={(e) => setTransport(e.target.value as typeof transport)}>
        <option value="stdio">stdio</option>
        <option value="http">http</option>
      </select>
      <label>Config (JSON)</label>
      <textarea
        value={configText}
        onChange={(e) => setConfigText(e.target.value)}
        rows={6}
        style={{ fontFamily: "ui-monospace, monospace", fontSize: "0.9em" }}
      />
      {err && <div className="modal-error">{err}</div>}
      <div className="modal-actions">
        <button onClick={onCancel} disabled={busy}>Cancelar</button>
        <button className="primary" onClick={submit} disabled={busy || !name.trim()}>
          {busy ? "Guardando…" : mode === "create" ? "Crear" : "Guardar"}
        </button>
      </div>
    </Modal>
  );
}
