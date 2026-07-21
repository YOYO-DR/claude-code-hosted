// GitHub settings + repos en tabla (UX-T.4 — sin paginación).

import { useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { api, ApiError } from "@/lib/api";
import { Modal } from "@/components/Modal";

interface GithubInfo {
  has_token: boolean;
  result?: {
    ok: boolean;
    user?: { login?: string };
    repos?: Array<{
      full_name: string;
      private: boolean;
      description: string | null;
      stargazers_count?: number;
      default_branch?: string;
      updated_at?: string;
    }>;
  };
}

export function GithubPage() {
  const qc = useQueryClient();
  const q = useQuery({
    queryKey: ["github"],
    queryFn: () => api<GithubInfo>("/api/v1/github/"),
  });
  const [token, setToken] = useState("");
  const [showForm, setShowForm] = useState(false);
  const [msg, setMsg] = useState<{ kind: "ok" | "err"; text: string } | null>(null);

  const m = useMutation({
    mutationFn: (tok: string) =>
      api<{ ok: boolean; result?: GithubInfo["result"] }>("/api/v1/github/", {
        method: "POST",
        body: { token: tok },
      }),
    onSuccess: (data) => {
      if (data.ok) {
        setMsg({ kind: "ok", text: "Token guardado y validado." });
        setToken("");
        setShowForm(false);
        void qc.invalidateQueries({ queryKey: ["github"] });
      } else {
        setMsg({ kind: "err", text: data.result?.ok === false ? "Token inválido" : "Error" });
      }
    },
    onError: (err: unknown) => {
      if (err instanceof ApiError) {
        const body = (err.body ?? {}) as { error?: string };
        setMsg({ kind: "err", text: `Error ${err.status}: ${body.error ?? "?"}` });
      } else {
        setMsg({ kind: "err", text: String(err) });
      }
    },
  });

  if (q.isLoading) return <p>Cargando…</p>;
  if (q.error) return <p className="msg error">Error: {String(q.error)}</p>;
  const info = q.data;
  const repos = info?.result?.repos ?? [];
  const login = info?.result?.user?.login;

  // Ordenar por stars desc; el resto alfabético
  const sortedRepos = [...repos].sort((a, b) =>
    (b.stargazers_count ?? 0) - (a.stargazers_count ?? 0)
       || a.full_name.localeCompare(b.full_name),
  );

  return (
    <div>
      <h1>GitHub</h1>
      {msg && (
        <div className={`msg ${msg.kind === "ok" ? "info" : "error"}`}>{msg.text}</div>
      )}

      <h2>Estado del token</h2>
      <p>
        {info?.has_token
          ? <>✓ Token guardado. {login && <>Autenticado como <code>{login}</code>.</>}</>
          : "✗ No hay token guardado."}
      </p>
      <button onClick={() => setShowForm(true)} className="primary">
        {info?.has_token ? "Reemplazar token" : "Configurar token"}
      </button>

      {showForm && (
        <Modal
          open
          title={info?.has_token ? "Reemplazar token" : "Configurar token"}
          onCancel={() => setShowForm(false)}
        >
          <p className="meta">
            El token se cifra con Fernet y se guarda en BD. Nunca se re-muestra.
          </p>
          <input
            type="password"
            placeholder="ghp_…"
            value={token}
            onChange={(e) => setToken(e.target.value)}
            autoComplete="off"
          />
          <div className="modal-actions">
            <button onClick={() => setShowForm(false)} disabled={m.isPending}>Cancelar</button>
            <button
              className="primary"
              onClick={() => token.trim() && m.mutate(token.trim())}
              disabled={m.isPending || !token.trim()}
            >
              {m.isPending ? "Validando…" : "Guardar y validar"}
            </button>
          </div>
        </Modal>
      )}

      {repos.length > 0 && (
        <>
          <h2>Repos con acceso ({repos.length})</h2>
          <table className="sessions-table">
            <thead>
              <tr>
                <th>Repo</th>
                <th>Descripción</th>
                <th>★ Stars</th>
                <th>Default</th>
                <th>Actualizado</th>
              </tr>
            </thead>
            <tbody>
              {sortedRepos.map((r) => (
                <tr key={r.full_name}>
                  <td>
                    <code>{r.full_name}</code>
                    {r.private && (
                      <span className="tag warn" style={{ marginLeft: "0.4rem" }}>private</span>
                    )}
                  </td>
                  <td className="meta">{r.description ?? "—"}</td>
                  <td style={{ fontFamily: "ui-monospace, monospace", textAlign: "right" }}>
                    {r.stargazers_count ?? "—"}
                  </td>
                  <td><code>{r.default_branch ?? "—"}</code></td>
                  <td className="meta">
                    {r.updated_at ? new Date(r.updated_at).toLocaleDateString() : "—"}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </>
      )}
    </div>
  );
}
