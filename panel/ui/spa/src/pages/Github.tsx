// GitHub settings (paridad 1:1 con /github/ legacy).
// Form para pegar/validar el PAT. GET /api/v1/github/ muestra el estado actual.
// POST /api/v1/github/ {token} valida y guarda cifrado.

import { useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { api, ApiError } from "@/lib/api";

interface GithubInfo {
  has_token: boolean;
  result?: { ok: boolean; user?: { login?: string }; repos?: Array<{ full_name: string; private: boolean }> };
}

export function GithubPage() {
  const qc = useQueryClient();
  const q = useQuery({
    queryKey: ["github"],
    queryFn: () => api<GithubInfo>("/api/v1/github/"),
  });
  const [token, setToken] = useState("");
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

  return (
    <div>
      <h1>GitHub</h1>
      {msg && (
        <div className={`msg ${msg.kind === "ok" ? "info" : "error"}`}>{msg.text}</div>
      )}

      <h2>Token actual</h2>
      {info?.has_token ? (
        <p>
          ✓ Token guardado. {login && <>Autenticado como <code>{login}</code>.</>}
        </p>
      ) : (
        <p>No hay token guardado.</p>
      )}

      <h2>Reemplazar / configurar token</h2>
      <form
        onSubmit={(e) => {
          e.preventDefault();
          if (!token.trim()) return;
          m.mutate(token.trim());
        }}
        style={{ display: "grid", gap: "0.5rem", maxWidth: 480 }}
      >
        <input
          type="password"
          placeholder="ghp_…"
          value={token}
          onChange={(e) => setToken(e.target.value)}
          autoComplete="off"
          required
        />
        <button type="submit" className="primary" disabled={m.isPending}>
          {m.isPending ? "Validando…" : "Guardar y validar"}
        </button>
      </form>
      <p className="meta">
        El token se cifra con Fernet y se guarda en BD. Nunca se re-muestra.
      </p>

      {repos.length > 0 && (
        <>
          <h2>Repos con acceso ({repos.length})</h2>
          <ul>
            {repos.slice(0, 50).map((r) => (
              <li key={r.full_name}>
                <code>{r.full_name}</code>
                {r.private && <span className="tag warn" style={{ marginLeft: "0.4rem" }}>private</span>}
              </li>
            ))}
            {repos.length > 50 && <li className="meta">… y {repos.length - 50} más</li>}
          </ul>
        </>
      )}
    </div>
  );
}