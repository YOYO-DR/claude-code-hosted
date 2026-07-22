// Vista Modelos (FASE D.1/D.2 + UX-T) — tabla con modal + sin window.confirm.

import { useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { api, ApiError } from "@/lib/api";
import { Modal } from "@/components/Modal";

interface ModelProfile {
  id: number;
  name: string;
  provider: "anthropic" | "minimax" | "custom";
  base_url: string | null;
  model: string;
  max_context_tokens: number | null;
  auto_compact_threshold: number | null;
  has_token: boolean;
  updated_at: string;
}

interface TestResult {
  ok: boolean;
  status?: number;
  error?: string;
}

const PROVIDERS = ["anthropic", "minimax", "custom"] as const;

export function ModelsPage() {
  const qc = useQueryClient();
  const q = useQuery({
    queryKey: ["models"],
    queryFn: () => api<ModelProfile[]>("/api/v1/models/"),
  });
  const [showCreate, setShowCreate] = useState(false);
  const [editing, setEditing] = useState<ModelProfile | null>(null);
  const [deleting, setDeleting] = useState<ModelProfile | null>(null);
  const [testing, setTesting] = useState<ModelProfile | null>(null);
  const [testResult, setTestResult] = useState<TestResult | null>(null);

  const deleteMut = useMutation({
    mutationFn: (id: number) =>
      api(`/api/v1/models/${id}/delete/`, { method: "DELETE" }),
    onSuccess: () => {
      setDeleting(null);
      qc.invalidateQueries({ queryKey: ["models"] });
    },
  });

  if (q.isLoading) return <p>Cargando…</p>;
  if (q.error) return <p className="msg error">Error: {String(q.error)}</p>;
  const data = q.data ?? [];

  return (
    <div>
      <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center" }}>
        <h1>Modelos</h1>
        <button className="primary" onClick={() => setShowCreate(true)}>+ Nuevo</button>
      </div>

      {showCreate && (
        <ModelFormModal
          mode="create"
          onSaved={() => { setShowCreate(false); qc.invalidateQueries({ queryKey: ["models"] }); }}
          onCancel={() => setShowCreate(false)}
        />
      )}
      {editing && (
        <ModelFormModal
          mode="edit"
          model={editing}
          onSaved={() => { setEditing(null); qc.invalidateQueries({ queryKey: ["models"] }); }}
          onCancel={() => setEditing(null)}
        />
      )}
      {deleting && (
        <Modal
          open
          title={`Borrar modelo "${deleting.name}"?`}
          variant="confirm"
          confirmLabel="Borrar"
          danger
          busy={deleteMut.isPending}
          onConfirm={() => deleteMut.mutate(deleting.id)}
          onCancel={() => setDeleting(null)}
        >
          <p>
            Esta acción no se puede deshacer. El token cifrado en BD se
            descarta junto con el perfil.
          </p>
          {deleteMut.error && (
            <div className="modal-error">{String(deleteMut.error)}</div>
          )}
        </Modal>
      )}

      {testing && (
        <Modal
          open
          title={`Probar "${testing.name}"`}
          variant="alert"
          busy={testResult === null}
          onCancel={() => { setTesting(null); setTestResult(null); }}
        >
          {testResult === null ? (
            <p className="modal-loading">Probando conexión con el endpoint…</p>
          ) : testResult.ok ? (
            <p className="msg info">
              ✓ Alcanzable — HTTP {testResult.status ?? 200}.
              El endpoint responde correctamente con el token configurado.
            </p>
          ) : (
            <div className="msg error">
              ✗ HTTP {testResult.status ?? "?"} · {testResult.error ?? "error"}
            </div>
          )}
        </Modal>
      )}

      {data.length === 0 && <p>No hay modelos.</p>}
      {data.length > 0 && (
        <table className="sessions-table">
          <thead>
            <tr>
              <th>Nombre</th>
              <th>Proveedor</th>
              <th>Modelo</th>
              <th>Base URL</th>
              <th>Token</th>
              <th>Acciones</th>
            </tr>
          </thead>
          <tbody>
            {data.map((m) => (
              <tr key={m.id}>
                <td><strong>{m.name}</strong></td>
                <td>{m.provider}</td>
                <td><code style={{ fontSize: "0.85em" }}>{m.model}</code></td>
                <td>
                  <code style={{ fontSize: "0.8em", color: "var(--muted)" }}>
                    {m.base_url ?? "—"}
                  </code>
                </td>
                <td>
                  {m.has_token
                    ? <span style={{ color: "#1a7f37", fontWeight: 600 }}>token</span>
                    : <span style={{ color: "#cf222e", fontWeight: 600 }}>sin token</span>}
                </td>
                <td style={{ display: "flex", gap: "0.3rem", flexWrap: "nowrap" }}>
                  <button onClick={async () => {
                    setTesting(m);
                    setTestResult(null);
                    try {
                      const r = await api<TestResult>(`/api/v1/models/${m.id}/test/`, { method: "POST" });
                      setTestResult(r);
                    } catch (e) {
                      setTestResult({ ok: false, error: String(e) });
                    }
                  }}>Probar</button>
                  <button onClick={() => setEditing(m)}>Editar</button>
                  <button className="danger" onClick={() => setDeleting(m)}>Borrar</button>
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      )}
    </div>
  );
}

function ModelFormModal({
  mode,
  model,
  onSaved,
  onCancel,
}: {
  mode: "create" | "edit";
  model?: ModelProfile;
  onSaved: () => void;
  onCancel: () => void;
}) {
  const [name, setName] = useState(model?.name ?? "");
  const [provider, setProvider] = useState<typeof PROVIDERS[number]>(model?.provider ?? "anthropic");
  const [modelName, setModelName] = useState(model?.model ?? "");
  const [baseUrl, setBaseUrl] = useState(model?.base_url ?? "");
  const [maxCtx, setMaxCtx] = useState(model?.max_context_tokens?.toString() ?? "");
  const [autoCompact, setAutoCompact] = useState(model?.auto_compact_threshold?.toString() ?? "");
  const [token, setToken] = useState("");
  const [err, setErr] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  const saveMut = useMutation({
    mutationFn: (body: Record<string, unknown>) =>
      mode === "create"
        ? api("/api/v1/models/create/", { method: "POST", body })
        : api(`/api/v1/models/${model!.id}/update/`, { method: "PATCH", body }),
    onSuccess: () => { setBusy(false); onSaved(); },
    onError: (e: unknown) => {
      setBusy(false);
      if (e instanceof ApiError) {
        const b = (e.body ?? {}) as { error?: string };
        setErr(b.error ?? `Error ${e.status}`);
      } else {
        setErr(String(e));
      }
    },
  });

  const submit = () => {
    setErr(null);
    if (!name.trim() || !modelName.trim()) {
      return setErr("nombre y modelo son requeridos");
    }
    const body: Record<string, unknown> = {
      name: name.trim(),
      provider,
      model: modelName.trim(),
      base_url: baseUrl.trim() || null,
      max_context_tokens: maxCtx.trim() ? Number(maxCtx.trim()) : null,
      auto_compact_threshold: autoCompact.trim() ? Number(autoCompact.trim()) : null,
    };
    if (mode === "create") {
      body.auth_token = token.trim() || null;
    } else if (token.trim()) {
      body.auth_token = token.trim();
    }
    setBusy(true);
    saveMut.mutate(body);
  };

  return (
    <Modal
      open
      variant="custom"
      title={mode === "create" ? "Nuevo modelo" : `Editar "${model?.name}"`}
      onCancel={onCancel}
    >
      <label>Nombre (único) *</label>
      <input value={name} onChange={(e) => setName(e.target.value)} required maxLength={100} />
      <label>Proveedor</label>
      <select value={provider} onChange={(e) => setProvider(e.target.value as typeof PROVIDERS[number])}>
        {PROVIDERS.map((p) => <option key={p} value={p}>{p}</option>)}
      </select>
      <label>Modelo *</label>
      <input
        placeholder="e.g. claude-3-5-sonnet-20241022"
        value={modelName}
        onChange={(e) => setModelName(e.target.value)}
        required
      />
      <label>Base URL (opcional)</label>
      <input
        placeholder="e.g. https://api.minimax.io/anthropic"
        value={baseUrl}
        onChange={(e) => setBaseUrl(e.target.value)}
      />
      <label>Máx. contexto en tokens (opcional — corrige la barra de contexto)</label>
      <input
        type="number"
        min={1}
        placeholder="e.g. 200000"
        value={maxCtx}
        onChange={(e) => setMaxCtx(e.target.value)}
      />
      <label>Umbral de auto-compact % (opcional, 1-100)</label>
      <input
        type="number"
        min={1}
        max={100}
        placeholder="e.g. 80"
        value={autoCompact}
        onChange={(e) => setAutoCompact(e.target.value)}
      />
      <label>
        {mode === "create" ? "Token (write-only — no se muestra tras guardar)" : "Nuevo token (vacío = mantener actual)"}
      </label>
      <input
        type="password"
        value={token}
        onChange={(e) => setToken(e.target.value)}
        autoComplete="off"
        placeholder="sk-…"
      />
      {err && <div className="modal-error">{err}</div>}
      <div className="modal-actions">
        <button onClick={onCancel} disabled={busy}>Cancelar</button>
        <button className="primary" onClick={submit} disabled={busy}>
          {busy ? "Guardando…" : mode === "create" ? "Crear" : "Guardar"}
        </button>
      </div>
    </Modal>
  );
}
