// Lista de proyectos (UX-T.2) — tabla con edit + delete + create.
// Reemplaza el placeholder. Consume /api/v1/projects/ y CRUD endpoints.

import { useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { api } from "@/lib/api";
import { Modal } from "@/components/Modal";

interface Project {
  slug: string;
  name: string;
  path: string;
  status: string;
  github_repo: string | null;
  github_enabled: boolean;
  github_warn_no_push: boolean;
}

interface ModelProfile {
  id: number;
  name: string;
  provider: string;
  has_token: boolean;
}

export function ProjectsPage() {
  const qc = useQueryClient();
  const q = useQuery({
    queryKey: ["projects"],
    queryFn: () => api<Project[]>("/api/v1/projects/"),
  });
  const modelsQ = useQuery({
    queryKey: ["models"],
    queryFn: () => api<ModelProfile[]>("/api/v1/models/"),
  });
  const [showCreate, setShowCreate] = useState(false);
  const [editing, setEditing] = useState<Project | null>(null);
  const [archiving, setArchiving] = useState<Project | null>(null);

  const archiveMut = useMutation({
    mutationFn: (slug: string) =>
      api(`/api/v1/projects/${slug}/delete/`, { method: "DELETE" }),
    onSuccess: () => {
      setArchiving(null);
      qc.invalidateQueries({ queryKey: ["projects"] });
    },
  });

  if (q.isLoading) return <p>Cargando…</p>;
  if (q.error) return <p className="msg error">Error: {String(q.error)}</p>;
  const data = q.data ?? [];

  return (
    <div>
      <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center" }}>
        <h1>Proyectos</h1>
        <button className="primary" onClick={() => setShowCreate(true)}>
          + Nuevo
        </button>
      </div>

      {showCreate && (
        <CreateProjectModal
          onCancel={() => setShowCreate(false)}
        />
      )}
      {editing && (
        <EditProjectModal
          project={editing}
          models={modelsQ.data ?? []}
          onSaved={() => { setEditing(null); qc.invalidateQueries({ queryKey: ["projects"] }); }}
          onCancel={() => setEditing(null)}
        />
      )}
      {archiving && (
        <Modal
          open
          title={`Archivar "${archiving.slug}"?`}
          variant="confirm"
          confirmLabel="Archivar"
          danger
          busy={archiveMut.isPending}
          onConfirm={() => archiveMut.mutate(archiving.slug)}
          onCancel={() => setArchiving(null)}
        >
          <p>
            El proyecto se moverá a <code>archived</code> y dejará de aparecer
            en esta lista. Sus sesiones ya cerradas siguen registrándose en
            <code> /sessions/</code> para auditoría.
          </p>
          {archiveMut.error && (
            <div className="modal-error">
              {String(archiveMut.error)}
            </div>
          )}
        </Modal>
      )}

      {data.length === 0 && <p>No hay proyectos.</p>}
      {data.length > 0 && (
        <table className="sessions-table">
          <thead>
            <tr>
              <th>Slug</th>
              <th>Nombre</th>
              <th>Path</th>
              <th>GitHub repo</th>
              <th>Warn</th>
              <th>Acciones</th>
            </tr>
          </thead>
          <tbody>
            {data.map((p) => (
              <tr key={p.slug}>
                <td>
                  <a href={`/projects/${p.slug}/start/`}>
                    <code>{p.slug}</code>
                  </a>
                </td>
                <td>{p.name}</td>
                <td>
                  <code style={{ fontSize: "0.8em", color: "var(--muted)" }}>
                    {p.path}
                  </code>
                </td>
                <td>
                  {p.github_enabled && p.github_repo ? (
                    <code>{p.github_repo}</code>
                  ) : (
                    <span className="meta">—</span>
                  )}
                </td>
                <td>
                  {p.github_warn_no_push && (
                    <span className="tag warn">sin push</span>
                  )}
                </td>
                <td style={{ display: "flex", gap: "0.3rem", flexWrap: "nowrap" }}>
                  <a href={`/projects/${p.slug}/start/`}>
                    <button>▶ Start</button>
                  </a>
                  <button onClick={() => setEditing(p)}>Editar</button>
                  <button className="danger" onClick={() => setArchiving(p)}>
                    Archivar
                  </button>
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      )}
    </div>
  );
}

// --- Helpers compartidos entre Create y Edit ---

interface ProjectFormProps {
  initial?: {
    name?: string;
    github_repo?: string | null;
    github_enabled?: boolean;
    telegram_topic_id?: number | null;
    model_profile_id?: number | null;
  };
  models: ModelProfile[];
  /** Errores devueltos por el backend (para FK inválida etc). */
  serverError?: string | null;
  busy?: boolean;
  onSubmit: (data: Record<string, unknown>) => Promise<void>;
  onCancel: () => void;
}

function ProjectFormBody({
  initial = {},
  models,
  serverError,
  busy,
  onSubmit,
  onCancel,
}: ProjectFormProps) {
  const [name, setName] = useState(initial.name ?? "");
  const [githubRepo, setGithubRepo] = useState(initial.github_repo ?? "");
  const [githubEnabled, setGithubEnabled] = useState(initial.github_enabled ?? false);
  const [telegramTopicId, setTelegramTopicId] = useState(
    initial.telegram_topic_id != null ? String(initial.telegram_topic_id) : "",
  );
  const [modelProfileId, setModelProfileId] = useState(
    initial.model_profile_id != null ? String(initial.model_profile_id) : "",
  );
  const [err, setErr] = useState<string | null>(null);

  const submit = async (e: React.FormEvent) => {
    e.preventDefault();
    setErr(null);
    const body: Record<string, unknown> = {
      name: name.trim(),
      github_repo: githubRepo.trim() || null,
      github_enabled: githubEnabled,
    };
    if (telegramTopicId.trim() !== "") {
      const n = Number(telegramTopicId);
      if (Number.isNaN(n)) return setErr("telegram_topic_id debe ser numérico");
      body.telegram_topic_id = n;
    } else {
      body.telegram_topic_id = null;
    }
    if (modelProfileId !== "") {
      const n = Number(modelProfileId);
      if (Number.isNaN(n)) return setErr("model_profile_id debe ser numérico");
      body.model_profile_id = n;
    }
    try {
      await onSubmit(body);
    } catch (e) {
      setErr(String(e));
    }
  };

  return (
    <form onSubmit={submit}>
      <label>Nombre</label>
      <input value={name} onChange={(e) => setName(e.target.value)} required />
      <label>GitHub repo (owner/repo)</label>
      <input value={githubRepo} onChange={(e) => setGithubRepo(e.target.value)} placeholder="opcional" />
      <label>
        <input
          type="checkbox"
          checked={githubEnabled}
          onChange={(e) => setGithubEnabled(e.target.checked)}
        />{" "}
        Habilitar GitHub (MCP in-process para el agente)
      </label>
      <label>Telegram topic ID</label>
      <input
        value={telegramTopicId}
        onChange={(e) => setTelegramTopicId(e.target.value)}
        placeholder="opcional"
      />
      <label>Modelo (ModelProfile)</label>
      <select value={modelProfileId} onChange={(e) => setModelProfileId(e.target.value)}>
        <option value="">— sin asignar —</option>
        {models.map((m) => (
          <option key={m.id} value={m.id}>
            {m.name} ({m.provider}){!m.has_token ? " — sin token" : ""}
          </option>
        ))}
      </select>
      {(err || serverError) && <div className="modal-error">{err ?? serverError}</div>}
      <div className="modal-actions">
        <button type="button" onClick={onCancel} disabled={busy}>Cancelar</button>
        <button type="submit" className="primary" disabled={busy}>
          {busy ? "Guardando…" : "Guardar"}
        </button>
      </div>
    </form>
  );
}

// --- Create + Edit como Modales ---

function CreateProjectModal({
  onCancel,
}: {
  onCancel: () => void;
}) {
  // Para crear hace falta POST al endpoint legacy o nuevo. Como no hay
  // aún endpoint de create en /api/v1, enlazamos al flujo legacy.
  // Esta versión solo cubre edición; el alta se hace vía /admin/.
  return (
    <Modal open title="Nuevo proyecto" onCancel={onCancel}>
      <p className="meta">
        La creación de proyectos requiere clonar un repo y provisionar
        CLAUDE.md / .mcp.json — el alta se hace desde{" "}
        <a href="/admin/core/project/add/"><code>/admin/</code></a> o el
        legacy <a href="/projects/new/">/projects/new/</a> (en desarrollo).
        Edita uno existente desde la tabla.
      </p>
      <div className="modal-actions">
        <button className="primary" onClick={onCancel}>Cerrar</button>
      </div>
    </Modal>
  );
}

function EditProjectModal({
  project,
  models,
  onSaved,
  onCancel,
}: {
  project: Project;
  models: ModelProfile[];
  onSaved: () => void;
  onCancel: () => void;
}) {
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState<string | null>(null);
  const updateMut = useMutation({
    mutationFn: (body: Record<string, unknown>) =>
      api(`/api/v1/projects/${project.slug}/update/`, {
        method: "PATCH",
        body,
      }),
    onSuccess: () => { setBusy(false); onSaved(); },
    onError: (e: unknown) => {
      setBusy(false);
      const msg = (() => {
        try { return JSON.parse(String(e)).error ?? String(e); }
        catch { return String(e); }
      })();
      setErr(msg);
    },
  });
  return (
    <Modal open title={`Editar "${project.slug}"`} onCancel={onCancel}>
      <ProjectFormBody
        initial={{
          name: project.name,
          github_repo: project.github_repo,
          github_enabled: project.github_enabled,
        }}
        models={models}
        serverError={err}
        busy={busy}
        onSubmit={async (data) => { setBusy(true); await updateMut.mutateAsync(data); }}
        onCancel={onCancel}
      />
    </Modal>
  );
}
