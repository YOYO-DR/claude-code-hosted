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

  const startMut = useMutation({
    mutationFn: (slug: string) =>
      api<{ ok: boolean; id: string; status: string }>(
        "/api/v1/sessions/create/",
        { method: "POST", body: { slug } },
      ),
    onSuccess: (data) => {
      // Navegar a la sesión creada (o reutilizada) — UX consistente con ▶ Start
      window.location.href = `/sessions/${data.id}`;
    },
  });
  const [startingSlug, setStartingSlug] = useState<string | null>(null);


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
                  <button
                    onClick={() => { setStartingSlug(p.slug); startMut.mutate(p.slug); }}
                    disabled={startMut.isPending}
                    title="Arrancar worker para este proyecto"
                  >
                    {startMut.isPending && startingSlug === p.slug ? "…" : "▶ Start"}
                  </button>
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
    const body: Record<string, unknown> = {
      name: name.trim(),
      slug: slug.trim(),
    };
    if (modelProfileId) body.model_profile_id = Number(modelProfileId);
    if (permissionPolicyId) body.permission_policy_id = Number(permissionPolicyId);
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
          pattern="[a-z0-9][a-z0-9-]*"
          maxLength={64}
          placeholder="mi-proyecto"
        />
        <label>Modelo (ModelProfile)</label>
        <select value={modelProfileId} onChange={(e) => setModelProfileId(e.target.value)}>
          <option value="">— sin asignar —</option>
          {(optsQ.data?.model_profiles ?? []).map((m) => (
            <option key={m.id} value={m.id}>{m.name} ({m.provider})</option>
          ))}
        </select>
        <label>Permission policy</label>
        <select value={permissionPolicyId} onChange={(e) => setPermissionPolicyId(e.target.value)}>
          <option value="">— sin asignar —</option>
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
