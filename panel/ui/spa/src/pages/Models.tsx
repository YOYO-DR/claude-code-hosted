// Vista Modelos (FASE D.1/D.2) — CRUD de ModelProfile.
// Lista + creación + probar + borrar.

import { useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { api, ApiError } from "@/lib/api";

interface ModelProfile {
  id: number;
  name: string;
  provider: "anthropic" | "minimax" | "custom";
  base_url: string | null;
  model: string;
  has_token: boolean;
  updated_at: string;
}

const PROVIDERS = ["anthropic", "minimax", "custom"] as const;

export function ModelsPage() {
  const qc = useQueryClient();
  const q = useQuery({
    queryKey: ["models"],
    queryFn: () => api<ModelProfile[]>("/api/v1/models/"),
  });
  const [showCreate, setShowCreate] = useState(false);

  if (q.isLoading) return <p>Cargando…</p>;
  if (q.error) return <p className="msg error">Error: {String(q.error)}</p>;
  const data = q.data ?? [];

  return (
    <div>
      <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center" }}>
        <h1>Modelos</h1>
        <button className="primary" onClick={() => setShowCreate((v) => !v)}>
          {showCreate ? "Cancelar" : "+ Nuevo"}
        </button>
      </div>

      {showCreate && <CreateModel onSaved={() => { setShowCreate(false); void qc.invalidateQueries({ queryKey: ["models"] }); }} />}

      {data.length === 0 && <p>No hay modelos.</p>}
      <ul>
        {data.map((m) => (
          <li key={m.id}>
            <ModelRow model={m} onChanged={() => qc.invalidateQueries({ queryKey: ["models"] })} />
          </li>
        ))}
      </ul>
    </div>
  );
}

function ModelRow({ model, onChanged }: { model: ModelProfile; onChanged: () => void }) {
  const qc = useQueryClient();
  const [editing, setEditing] = useState(false);
  const [msg, setMsg] = useState<{ kind: "ok" | "err"; text: string } | null>(null);

  const testMut = useMutation<{ ok: boolean; status?: number; error?: string }, Error>({
    mutationFn: () => api(`/api/v1/models/${model.id}/test/`, { method: "POST" }),
    onSuccess: (data) => {
      if (data.ok) {
        setMsg({ kind: "ok", text: `OK (${data.status ?? 200})` });
      } else {
        setMsg({ kind: "err", text: data.error || `HTTP ${data.status ?? "?"}` });
      }
    },
    onError: (err: unknown) => setMsg({ kind: "err", text: String(err) }),
  });

  const deleteMut = useMutation({
    mutationFn: () => api(`/api/v1/models/${model.id}/delete/`, { method: "DELETE" }),
    onSuccess: () => {
      void qc.invalidateQueries({ queryKey: ["models"] });
      onChanged();
    },
  });

  return (
    <div>
      <div style={{ display: "flex", justifyContent: "space-between", alignItems: "flex-start" }}>
        <div style={{ flex: 1 }}>
          <strong>{model.name}</strong>{" "}
          <span className="meta">[{model.provider}]</span>{" "}
          {model.has_token
            ? <span className="tag" style={{ background: "#dfd", color: "#116329" }}>token</span>
            : <span className="tag" style={{ background: "#fee", color: "#842029" }}>sin token</span>}
          <br />
          <code style={{ fontSize: "0.85em" }}>{model.model}</code>
          {model.base_url && (
            <>
              <br />
              <code style={{ fontSize: "0.8em", color: "var(--muted)" }}>{model.base_url}</code>
            </>
          )}
        </div>
        <div style={{ display: "flex", flexDirection: "column", gap: "0.3rem", alignItems: "flex-end" }}>
          <button onClick={() => testMut.mutate()} disabled={testMut.isPending}>
            {testMut.isPending ? "..." : "Probar"}
          </button>
          <button onClick={() => setEditing((v) => !v)}>Editar</button>
          <button className="danger" onClick={() => {
            if (window.confirm(`¿Borrar modelo "${model.name}"?`)) deleteMut.mutate();
          }}>Borrar</button>
        </div>
      </div>
      {msg && (
        <div className={`msg ${msg.kind === "ok" ? "info" : "error"}`} style={{ marginTop: "0.4rem" }}>
          {msg.text}
        </div>
      )}
      {editing && (
        <EditModel model={model} onSaved={() => { setEditing(false); onChanged(); }} />
      )}
    </div>
  );
}

function CreateModel({ onSaved }: { onSaved: () => void }) {
  const [name, setName] = useState("");
  const [provider, setProvider] = useState<typeof PROVIDERS[number]>("anthropic");
  const [model, setModel] = useState("");
  const [baseUrl, setBaseUrl] = useState("");
  const [token, setToken] = useState("");
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  const submit = async (e: React.FormEvent) => {
    e.preventDefault();
    setError(null);
    setBusy(true);
    try {
      await api("/api/v1/models/create/", {
        method: "POST",
        body: {
          name,
          provider,
          model,
          base_url: baseUrl || null,
          auth_token: token || null,
        },
      });
      onSaved();
    } catch (err: unknown) {
      if (err instanceof ApiError) {
        const body = (err.body ?? {}) as { error?: string };
        setError(body.error ?? `Error ${err.status}`);
      } else {
        setError(String(err));
      }
    } finally {
      setBusy(false);
    }
  };

  return (
    <form onSubmit={submit} className="login" style={{ maxWidth: 480, margin: "0.5rem auto 1rem" }}>
      <h2 style={{ margin: 0 }}>Nuevo modelo</h2>
      {error && <div className="msg error">{error}</div>}
      <input placeholder="nombre (único)" value={name} onChange={(e) => setName(e.target.value)} required />
      <select value={provider} onChange={(e) => setProvider(e.target.value as typeof PROVIDERS[number])}>
        {PROVIDERS.map((p) => <option key={p} value={p}>{p}</option>)}
      </select>
      <input placeholder="modelo (e.g. claude-3-5-sonnet-20241022)" value={model} onChange={(e) => setModel(e.target.value)} required />
      <input placeholder="base_url (opcional)" value={baseUrl} onChange={(e) => setBaseUrl(e.target.value)} />
      <input type="password" placeholder="auth_token (opcional, write-only)" value={token} onChange={(e) => setToken(e.target.value)} autoComplete="off" />
      <button type="submit" className="primary" disabled={busy}>
        {busy ? "Guardando..." : "Crear"}
      </button>
    </form>
  );
}

function EditModel({ model, onSaved }: { model: ModelProfile; onSaved: () => void }) {
  const [name, setName] = useState(model.name);
  const [modelName, setModelName] = useState(model.model);
  const [baseUrl, setBaseUrl] = useState(model.base_url ?? "");
  const [token, setToken] = useState("");
  const [msg, setMsg] = useState<{ kind: "ok" | "err"; text: string } | null>(null);
  const [busy, setBusy] = useState(false);

  const submit = async (e: React.FormEvent) => {
    e.preventDefault();
    setBusy(true);
    setMsg(null);
    try {
      const body: Record<string, unknown> = { name, model: modelName, base_url: baseUrl || null };
      // Solo mandamos auth_token si el usuario escribió algo (dejar vacío
      // = no tocar el token actual).
      if (token) body.auth_token = token;
      await api(`/api/v1/models/${model.id}/update/`, {
        method: "PATCH",
        body,
      });
      setMsg({ kind: "ok", text: "Actualizado." });
      onSaved();
    } catch (err: unknown) {
      if (err instanceof ApiError) {
        const body = (err.body ?? {}) as { error?: string };
        setMsg({ kind: "err", text: body.error ?? `Error ${err.status}` });
      } else {
        setMsg({ kind: "err", text: String(err) });
      }
    } finally {
      setBusy(false);
    }
  };

  return (
    <form onSubmit={submit} className="login" style={{ maxWidth: 480, margin: "0.5rem auto 1rem" }}>
      {msg && <div className={`msg ${msg.kind === "ok" ? "info" : "error"}`}>{msg.text}</div>}
      <input placeholder="nombre" value={name} onChange={(e) => setName(e.target.value)} required />
      <input placeholder="modelo" value={modelName} onChange={(e) => setModelName(e.target.value)} required />
      <input placeholder="base_url" value={baseUrl} onChange={(e) => setBaseUrl(e.target.value)} />
      <input
        type="password"
        placeholder="nuevo auth_token (dejar vacío para no cambiar)"
        value={token}
        onChange={(e) => setToken(e.target.value)}
        autoComplete="off"
      />
      <button type="submit" className="primary" disabled={busy}>
        {busy ? "Guardando..." : "Actualizar"}
      </button>
    </form>
  );
}