// Lista de proyectos (UX-T.2) — tabla con edit + delete + create.
// Reemplaza el placeholder. Consume /api/v1/projects/ y CRUD endpoints.

import { useEffect, useState } from "react";
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
  const qActive = useQuery({
    queryKey: ["projects", "active"],
    queryFn: () => api<Project[]>("/api/v1/projects/?status=active"),
  });
  const qArchived = useQuery({
    queryKey: ["projects", "archived"],
    queryFn: () => api<Project[]>("/api/v1/projects/?status=archived"),
  });
  const modelsQ = useQuery({
    queryKey: ["models"],
    queryFn: () => api<ModelProfile[]>("/api/v1/models/"),
  });
  const [showCreate, setShowCreate] = useState(false);
  const [editing, setEditing] = useState<Project | null>(null);
  const [archiving, setArchiving] = useState<Project | null>(null);
  // SP4: hard-delete + recreate modales
  const [purging, setPurging] = useState<Project | null>(null);
  const [recreating, setRecreating] = useState<Project | null>(null);

  const archiveMut = useMutation({
    mutationFn: (slug: string) =>
      api(`/api/v1/projects/${slug}/delete/`, { method: "DELETE" }),
    onSuccess: () => {
      setArchiving(null);
      qc.invalidateQueries({ queryKey: ["projects"] });
    },
  });

  const startMut = useMutation({
    mutationFn: (slug: string) =>
      api<{ ok: boolean; id: string; status: string }>(
        "/api/v1/sessions/create/",
        { method: "POST", body: { slug } },
      ),
    onSuccess: (data) => {
      setStartingSlug(null);
      // Navegar a la sesión creada (o reutilizada) — UX consistente con ▶ Start
      window.location.href = `/sessions/${data.id}`;
    },
  });
  const [startingSlug, setStartingSlug] = useState<string | null>(null);

  const purgeMut = useMutation({
    mutationFn: (vars: { slug: string; body: Record<string, unknown> }) =>
      api(`/api/v1/projects/${vars.slug}/delete/`, {
        method: "POST",
        body: vars.body,
      }),
    onSuccess: () => {
      setPurging(null);
      qc.invalidateQueries({ queryKey: ["projects"] });
    },
  });

  const recreateMut = useMutation({
    mutationFn: (slug: string) =>
      api<{ ok: boolean; slug: string }>(
        `/api/v1/projects/${slug}/recreate/`,
        { method: "POST" },
      ),
    onSuccess: () => {
      setRecreating(null);
      qc.invalidateQueries({ queryKey: ["projects"] });
      // El listado ahora contendrá el nuevo activo con mismo slug.
    },
  });

  if (qActive.isLoading) return <p>Cargando…</p>;
  if (qActive.error) return <p className="msg error">Error: {String(qActive.error)}</p>;
  const active = qActive.data ?? [];
  const archived = qArchived.data ?? [];

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
          onSaved={() => {
            setShowCreate(false);
            qc.invalidateQueries({ queryKey: ["projects"] });
          }}
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
            en esta lista (visible en la sección inferior). El dir y las
            sesiones históricas se preservan para auditoría y para poder
            <strong> re-clonar</strong> después.
          </p>
          {archiveMut.error && (
            <div className="modal-error">
              <p style={{ margin: "0 0 0.5rem" }}>
                {(archiveMut.error as Error).message}
              </p>
              {(() => {
                const body = (archiveMut.error as { body?: { active_sessions?: Array<{ id: string; status: string }> } }).body;
                const active = body?.active_sessions;
                if (!active || active.length === 0) return null;
                return (
                  <div>
                    <p style={{ margin: "0 0 0.4rem", fontWeight: 600 }}>
                      Sesiones que debes parar primero:
                    </p>
                    <ul style={{ margin: 0, paddingLeft: "1.2rem" }}>
                      {active.map((s) => (
                        <li key={s.id}>
                          <a href={`/sessions/${s.id}`} target="_blank" rel="noreferrer">
                            <code>{s.id.slice(0, 8)}</code>
                          </a>{" "}
                          · <span className={`tag status-${s.status}`}>{s.status}</span>
                        </li>
                      ))}
                    </ul>
                  </div>
                );
              })()}
            </div>
          )}
        </Modal>
      )}
      {purging && (
        <PurgeProjectModal
          project={purging}
          busy={purgeMut.isPending}
          onCancel={() => setPurging(null)}
          onConfirm={(body) => purgeMut.mutate({ slug: purging.slug, body })}
          error={purgeMut.error}
        />
      )}
      {recreating && (
        <RecreateProjectModal
          project={recreating}
          busy={recreateMut.isPending}
          onCancel={() => setRecreating(null)}
          onConfirm={() => recreateMut.mutate(recreating.slug)}
          error={recreateMut.error}
        />
      )}

      {/* Activos */}
      <h2 style={{ fontSize: "1.05rem", marginTop: "1rem" }}>Activos</h2>
      {active.length === 0 ? (
        <p>No hay proyectos activos.</p>
      ) : (
        <ProjectTable
          rows={active}
          startMut={startMut}
          startingSlug={startingSlug}
          onEdit={setEditing}
          onArchive={setArchiving}
          onPurge={setPurging}
        />
      )}

      {/* Archivados (SP4): listados aparte, con Re-clonar + Eliminar. */}
      {archived.length > 0 && (
        <>
          <h2 style={{ fontSize: "1.05rem", marginTop: "1.5rem" }}>
            Archivados
          </h2>
          <p style={{ color: "var(--muted)", fontSize: "0.85em", margin: "0 0 0.5rem" }}>
            Proyectos archivados. Puedes <strong>re-clonar</strong> desde su
            repo original (se hard-delean y se vuelven a crear) o
            <strong> eliminar definitivamente</strong>.
          </p>
          <ArchivedProjectTable
            rows={archived}
            onRecreate={setRecreating}
            onPurge={setPurging}
          />
        </>
      )}
    </div>
  );
}

// --- Tabla de proyectos activos (UX-T.5 + SP4 columna estado + purgar) ---

interface ProjectTableProps {
  rows: Project[];
  startMut: ReturnType<typeof useMutation<unknown, Error, string>>;
  startingSlug: string | null;
  onEdit: (p: Project) => void;
  onArchive: (p: Project) => void;
  onPurge: (p: Project) => void;
}

function ProjectTable({
  rows,
  startMut,
  startingSlug,
  onEdit,
  onArchive,
  onPurge,
}: ProjectTableProps) {
  return (
    <table className="sessions-table">
      <thead>
        <tr>
          <th>Slug</th>
          <th>Nombre</th>
          <th>Path</th>
          <th>GitHub repo</th>
          <th>Estado</th>
          <th>Warn</th>
          <th>Acciones</th>
        </tr>
      </thead>
      <tbody>
        {rows.map((p) => (
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
              <StatusTag status={p.status} />
            </td>
            <td>
              {p.github_warn_no_push && (
                <span className="tag warn">sin push</span>
              )}
            </td>
            <td style={{ display: "flex", gap: "0.3rem", flexWrap: "nowrap" }}>
              <button
                onClick={() => startMut.mutate(p.slug)}
                disabled={startMut.isPending}
                title="Arrancar worker para este proyecto"
              >
                {startMut.isPending && startingSlug === p.slug ? "…" : "▶ Start"}
              </button>
              <button onClick={() => onEdit(p)}>Editar</button>
              <button className="danger" onClick={() => onArchive(p)}>
                Archivar
              </button>
              <button
                className="danger"
                onClick={() => onPurge(p)}
                title="Eliminar definitivamente (hard-delete)"
                style={{ fontSize: "0.85em" }}
              >
                🗑 Purga
              </button>
            </td>
          </tr>
        ))}
      </tbody>
    </table>
  );
}

// --- Tabla de proyectos archivados (solo Re-clonar + Purga) ---

function ArchivedProjectTable({
  rows,
  onRecreate,
  onPurge,
}: {
  rows: Project[];
  onRecreate: (p: Project) => void;
  onPurge: (p: Project) => void;
}) {
  return (
    <table className="sessions-table">
      <thead>
        <tr>
          <th>Slug</th>
          <th>Nombre</th>
          <th>GitHub repo</th>
          <th>Estado</th>
          <th>Acciones</th>
        </tr>
      </thead>
      <tbody>
        {rows.map((p) => (
          <tr key={p.slug} style={{ opacity: 0.85 }}>
            <td>
              <code>{p.slug}</code>
            </td>
            <td>{p.name}</td>
            <td>
              {p.github_enabled && p.github_repo ? (
                <code>{p.github_repo}</code>
              ) : (
                <span className="meta">—</span>
              )}
            </td>
            <td>
              <StatusTag status={p.status} />
            </td>
            <td style={{ display: "flex", gap: "0.3rem", flexWrap: "nowrap" }}>
              <button
                className="primary"
                onClick={() => onRecreate(p)}
                title="Hard-delete + re-clonar el repo + nueva fila activa"
              >
                ↻ Re-clonar
              </button>
              <button
                className="danger"
                onClick={() => onPurge(p)}
                style={{ fontSize: "0.85em" }}
              >
                🗑 Purga
              </button>
            </td>
          </tr>
        ))}
      </tbody>
    </table>
  );
}

// --- Status chip ---

function StatusTag({ status }: { status: string }) {
  const palette: Record<string, { bg: string; fg: string; label: string }> = {
    active: { bg: "#dafbe1", fg: "#1a7f37", label: "active" },
    archived: { bg: "#ddf4ff", fg: "#0550ae", label: "archived" },
    deleted: { bg: "#ffebe9", fg: "#cf222e", label: "deleted" },
  };
  const s = palette[status] ?? { bg: "#eaeef2", fg: "#57606a", label: status };
  return (
    <span
      className="tag"
      style={{ background: s.bg, color: s.fg, fontWeight: 600 }}
    >
      {s.label}
    </span>
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

interface FormOptions {
  model_profiles: Array<{ id: number; name: string; provider: string }>;
  permission_policies: Array<{ id: number; name: string; mode: string }>;
  gh_token_missing: boolean;
}

function CreateProjectModal({
  onSaved,
  onCancel,
}: {
  onSaved: (slug: string) => void;
  onCancel: () => void;
}) {
  const [name, setName] = useState("");
  const [slug, setSlug] = useState("");
  const [githubRepo, setGithubRepo] = useState("");
  const [githubEnabled, setGithubEnabled] = useState(false);
  const [modelProfileId, setModelProfileId] = useState("");
  const [permissionPolicyId, setPermissionPolicyId] = useState("");
  const [telegramTopicId, setTelegramTopicId] = useState("");
  const [autoSlug, setAutoSlug] = useState(true);
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState<string | null>(null);
  const [warnings, setWarnings] = useState<string[]>([]);

  const optsQ = useQuery({
    queryKey: ["projects", "form-options"],
    queryFn: () => api<FormOptions>("/api/v1/projects/form-options/"),
  });

  // Auto-derive slug from name (kebab-case) hasta que el usuario lo toque.
  useEffect(() => {
    if (!autoSlug) return;
    const derived = name
      .toLowerCase()
      .replace(/[^a-z0-9]+/g, "-")
      .replace(/^-|-$/g, "")
      .slice(0, 64);
    setSlug(derived);
  }, [name, autoSlug]);

  const ghMissing = optsQ.data?.gh_token_missing ?? false;
  const submit = async (e: React.FormEvent) => {
    e.preventDefault();
    setErr(null);
    setWarnings([]);
    if (!name.trim() || !slug.trim()) {
      setErr("nombre y slug son requeridos");
      return;
    }
    if (!modelProfileId) {
      setErr("elige un modelo — model_profile_id es requerido");
      return;
    }
    if (!permissionPolicyId) {
      setErr("elige una permission policy — permission_policy_id es requerido");
      return;
    }
    const body: Record<string, unknown> = {
      name: name.trim(),
      slug: slug.trim(),
      model_profile_id: Number(modelProfileId),
      permission_policy_id: Number(permissionPolicyId),
    };
    if (telegramTopicId.trim()) {
      const n = Number(telegramTopicId);
      if (Number.isNaN(n)) return setErr("telegram_topic_id debe ser numérico");
      body.telegram_topic_id = n;
    }
    if (githubEnabled) {
      if (!githubRepo.trim()) return setErr("github_repo obligatorio si github_enabled");
      body.github_repo = githubRepo.trim();
      body.github_enabled = true;
    }
    setBusy(true);
    try {
      const r = await api<{
        ok: boolean;
        slug: string;
        warnings?: string[];
      }>("/api/v1/projects/create/", { method: "POST", body });
      if (r.warnings && r.warnings.length) setWarnings(r.warnings);
      onSaved(r.slug);
    } catch (e: unknown) {
      const m = (() => {
        try { return JSON.parse(String(e)).error ?? String(e); }
        catch { return String(e); }
      })();
      setErr(m);
    } finally {
      setBusy(false);
    }
  };

  return (
    <Modal open variant="custom" title="Nuevo proyecto" onCancel={onCancel}>
      <form onSubmit={submit}>
        <label>Nombre *</label>
        <input
          value={name}
          onChange={(e) => setName(e.target.value)}
          required
          maxLength={200}
          placeholder="Mi Proyecto"
        />
        <label>Slug (kebab-case, inmutable) *</label>
        <input
          value={slug}
          onChange={(e) => { setAutoSlug(false); setSlug(e.target.value); }}
          required
          pattern="[a-z0-9][a-z0-9\-]*"
          maxLength={64}
          placeholder="mi-proyecto"
        />
        <label>Modelo (ModelProfile) *</label>
        <select
          value={modelProfileId}
          onChange={(e) => setModelProfileId(e.target.value)}
          required
        >
          <option value="" disabled>— elige un modelo —</option>
          {(optsQ.data?.model_profiles ?? []).map((m) => (
            <option key={m.id} value={m.id}>{m.name} ({m.provider})</option>
          ))}
        </select>
        <label>Permission policy *</label>
        <select
          value={permissionPolicyId}
          onChange={(e) => setPermissionPolicyId(e.target.value)}
          required
        >
          <option value="" disabled>— elige una policy —</option>
          {(optsQ.data?.permission_policies ?? []).map((p) => (
            <option key={p.id} value={p.id}>{p.name} ({p.mode})</option>
          ))}
        </select>
        <label>
          <input
            type="checkbox"
            checked={githubEnabled}
            onChange={(e) => setGithubEnabled(e.target.checked)}
          />{" "}
          Habilitar GitHub (MCP in-process para el agente)
        </label>
        {githubEnabled && (
          <>
            <label>GitHub repo (owner/repo)</label>
            <input
              value={githubRepo}
              onChange={(e) => setGithubRepo(e.target.value)}
              placeholder="owner/repo"
              required
            />
            {ghMissing && (
              <div className="modal-error">
                github_enabled=True pero no hay token de GitHub guardado.
                Ve a <a href="/github">/github</a> y pega uno primero.
              </div>
            )}
          </>
        )}
        <label>Telegram topic ID (opcional)</label>
        <input
          value={telegramTopicId}
          onChange={(e) => setTelegramTopicId(e.target.value)}
          placeholder="opcional"
        />
        {err && <div className="modal-error">{err}</div>}
        {warnings.map((w, i) => (
          <div key={i} className="msg warning">{w}</div>
        ))}
        <div className="modal-actions">
          <button type="button" onClick={onCancel} disabled={busy}>Cancelar</button>
          <button type="submit" className="primary" disabled={busy || (githubEnabled && ghMissing)}>
            {busy ? "Clonando + provisionando…" : "Crear proyecto"}
          </button>
        </div>
      </form>
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
    <Modal open variant="custom" title={`Editar "${project.slug}"`} onCancel={onCancel}>
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


// --- SP4: Hard-delete (purga) — modal con doble confirmación ---

function PurgeProjectModal({
  project,
  busy,
  error,
  onConfirm,
  onCancel,
}: {
  project: Project;
  busy: boolean;
  error: unknown;
  onConfirm: (body: Record<string, unknown>) => void;
  onCancel: () => void;
}) {
  const [purgeSessions, setPurgeSessions] = useState(true);
  const [purgeFiles, setPurgeFiles] = useState(true);
  const [confirmSlug, setConfirmSlug] = useState("");
  const ready = confirmSlug === project.slug;

  return (
    <Modal open variant="custom" title={`Eliminar definitivamente "${project.slug}"?`} onCancel={onCancel}>
      <p style={{ color: "var(--err-fg)", fontWeight: 600 }}>
        ⚠️ Operación destructiva. El proyecto deja de existir para siempre
        (a menos que lo vuelvas a crear manualmente).
      </p>
      <label style={{ display: "flex", gap: "0.4rem", alignItems: "center", margin: "0.4rem 0" }}>
        <input
          type="checkbox"
          checked={purgeSessions}
          onChange={(e) => setPurgeSessions(e.target.checked)}
        />
        Borrar también todas las sesiones históricas (eventos, costos, mensajes)
      </label>
      <label style={{ display: "flex", gap: "0.4rem", alignItems: "center", margin: "0.4rem 0" }}>
        <input
          type="checkbox"
          checked={purgeFiles}
          onChange={(e) => setPurgeFiles(e.target.checked)}
        />
        Borrar el directorio en disco
        <code style={{ fontSize: "0.85em" }}>{project.path}</code>
      </label>
      <p style={{ margin: "0.6rem 0 0.3rem" }}>
        Para confirmar, escribe el slug exacto <code>{project.slug}</code>:
      </p>
      <input
        value={confirmSlug}
        onChange={(e) => setConfirmSlug(e.target.value)}
        placeholder={project.slug}
        autoFocus
        data-testid="purge-confirm-slug"
      />
      {Boolean(error) && (
        <div className="modal-error" style={{ marginTop: "0.5rem" }}>
          <p style={{ margin: "0 0 0.5rem" }}>{String((error as Error)?.message ?? error)}</p>
        </div>
      )}
      <div className="modal-actions">
        <button type="button" onClick={onCancel} disabled={busy}>Cancelar</button>
        <button
          type="button"
          className="danger"
          disabled={!ready || busy}
          data-testid="purge-confirm"
          onClick={() =>
            onConfirm({
              hard: true,
              confirm_slug: project.slug,
              purge_sessions: purgeSessions,
              purge_files: purgeFiles,
            })
          }
        >
          {busy ? "Eliminando…" : "Eliminar definitivamente"}
        </button>
      </div>
    </Modal>
  );
}


// --- SP4: Re-clonar desde repo (archived/deleted → nuevo active) ---

function RecreateProjectModal({
  project,
  busy,
  error,
  onConfirm,
  onCancel,
}: {
  project: Project;
  busy: boolean;
  error: unknown;
  onConfirm: () => void;
  onCancel: () => void;
}) {
  return (
    <Modal open variant="custom" title={`Re-clonar "${project.slug}"?`} onCancel={onCancel}>
      <p>
        Esto <strong>hard-delea</strong> el proyecto actual (sesiones + dir en
        disco <code>{project.path}</code>) y crea uno <strong>nuevo</strong> con
        el mismo slug, re-clonando desde su repo original:
      </p>
      <ul style={{ margin: "0.3rem 0 0.6rem 1.2rem" }}>
        <li>
          Repo:{" "}
          {project.github_enabled && project.github_repo ? (
            <code>{project.github_repo}</code>
          ) : (
            <em>(ninguno — se hará git init local)</em>
          )}
        </li>
        <li>
          Rama: <code>agent/{project.slug}</code>
        </li>
      </ul>
      <p style={{ color: "var(--muted)", fontSize: "0.85em" }}>
        Las sesiones históricas se borran. Si solo quieres conservar el
        audit log sin tocar nada, usa Archivar (no purga).
      </p>
      {Boolean(error) && (
        <div className="modal-error" style={{ marginTop: "0.5rem" }}>
          <p style={{ margin: "0 0 0.5rem" }}>{String((error as Error)?.message ?? error)}</p>
        </div>
      )}
      <div className="modal-actions">
        <button type="button" onClick={onCancel} disabled={busy}>Cancelar</button>
        <button
          type="button"
          className="primary"
          disabled={busy}
          data-testid="recreate-confirm"
          onClick={onConfirm}
        >
          {busy ? "Re-clonando…" : "Re-clonar y arrancar"}
        </button>
      </div>
    </Modal>
  );
}
