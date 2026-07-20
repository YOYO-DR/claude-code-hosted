// Vista Sesión (FASE C.4) — Layout OpenHands: chat izquierda, panel lateral derecho.
//
// Chat: discriminated union UIEvent v1 por kind (FASE B).
// Panel lateral: placeholders (FASE C.5 archivos+diff, C.6 rama).

import { useEffect, useRef, useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { api } from "@/lib/api";
import { openSessionWs, type RawEventMessage } from "@/lib/ws";
import type { UIEvent, UIEventKind } from "@/types/uievents";
import { ProjectTree, ProjectDiff } from "@/components/ProjectTree";

interface Session {
  id: string;
  project: string;
  project_slug: string;
  status: string;
  total_cost_usd: number;
  github_warn_no_push: boolean;
}

interface Bubble {
  key: string;
  kind: UIEventKind;
  raw: RawEventMessage;
  ui: UIEvent | null;
  result?: RawEventMessage;
}

function useSession(id: string) {
  return useQuery({
    queryKey: ["session", id],
    queryFn: () => api<Session>(`/api/v1/sessions/${id}/`),
  });
}

function useSessionIdFromPath(): string {
  const path = window.location.pathname;
  const m = path.match(/^\/sessions\/([0-9a-f-]{36})/);
  return m && m[1] ? m[1] : "";
}

export function SessionPage() {
  const sid = useSessionIdFromPath();
  const sessQ = useSession(sid);
  const [bubbles, setBubbles] = useState<Bubble[]>([]);
  const [wsState, setWsState] = useState<string>("connecting");
  const [input, setInput] = useState("");
  const [tab, setTab] = useState<"archivos" | "cambios" | "rama">("archivos");
  const wsRef = useRef<ReturnType<typeof openSessionWs> | null>(null);
  const seenSeq = useRef<Set<number>>(new Set());
  const scrollerRef = useRef<HTMLDivElement | null>(null);

  useEffect(() => {
    if (!sessQ.data) return;
    seenSeq.current.clear();
    setBubbles([]);
    const ws = openSessionWs(
      sid,
      {
        onEvent: (msg) => {
          if (seenSeq.current.has(msg.seq)) return;
          seenSeq.current.add(msg.seq);
          const ui = msg.ui_event;
          if (!ui) return;
          setBubbles((prev) => {
            if (ui.kind === "tool_result") {
              const toolUseId = (ui.payload as { tool_use_id?: string }).tool_use_id;
              if (toolUseId) {
                const idx = prev.findIndex(
                  (b) =>
                    b.kind === "tool_call" &&
                    ((b.ui?.payload as { tool_use_id?: string })?.tool_use_id ?? null) ===
                      toolUseId,
                );
                if (idx >= 0) {
                  const copy = prev.slice();
                  const target = copy[idx];
                  if (target) {
                    copy[idx] = { ...target, result: msg };
                    return copy;
                  }
                }
              }
            }
            return [...prev, { key: `${ui.kind}-${msg.seq}`, kind: ui.kind, raw: msg, ui }];
          });
        },
        onStateChange: setWsState,
      },
      0,
    );
    wsRef.current = ws;
    return () => {
      ws.close();
      wsRef.current = null;
    };
  }, [sid, sessQ.data]);

  useEffect(() => {
    const el = scrollerRef.current;
    if (el) el.scrollTop = el.scrollHeight;
  }, [bubbles.length]);

  const qc = useQueryClient();
  const sendMut = useMutation({
    mutationFn: (text: string) =>
      api<{ ok: true }>(`/api/v1/sessions/${sid}/message/`, {
        method: "POST",
        body: { text },
      }),
    onSuccess: () => qc.invalidateQueries({ queryKey: ["session", sid] }),
  });
  const stopMut = useMutation({
    mutationFn: () => api(`/api/v1/sessions/${sid}/stop/`, { method: "POST" }),
    onSuccess: () => qc.invalidateQueries({ queryKey: ["session", sid] }),
  });
  const resolvePerm = useMutation({
    mutationFn: ({ id, answer }: { id: string; answer: "allow" | "deny" }) =>
      api(`/api/v1/permissions/${id}/resolve/`, { method: "POST", body: { answer } }),
  });

  if (!sid) return <p className="msg error">URL inválida — falta session id.</p>;
  if (sessQ.isLoading) return <p>Cargando sesión…</p>;
  if (sessQ.error) return <p className="msg error">Error: {String(sessQ.error)}</p>;
  const sess = sessQ.data;
  if (!sess) return null;

  const sendOnEnter: React.KeyboardEventHandler<HTMLTextAreaElement> = (e) => {
    if ((e.ctrlKey || e.metaKey) && e.key === "Enter") {
      e.preventDefault();
      const text = input.trim();
      if (!text) return;
      sendMut.mutate(text);
      setInput("");
    }
  };

  return (
    <div>
      <p style={{ margin: "0 0 0.6rem" }}>
        <a href="/sessions">← Sesiones</a>
      </p>
      <h1>
        Sesión <span style={{ color: "var(--muted)", fontSize: "0.7em" }}>{sid.slice(0, 8)}</span>
      </h1>
      <div style={{ display: "flex", gap: "1rem", flexWrap: "wrap", marginBottom: "0.6rem" }}>
        <span>Proyecto: <strong>{sess.project}</strong></span>
        <span>
          Estado:{" "}
          <span style={{ border: "1px solid var(--border)", padding: "0 0.4rem", borderRadius: 4 }}>
            {sess.status}
          </span>
        </span>
        <span>Costo: ${sess.total_cost_usd.toFixed(4)}</span>
        <span style={{ color: "var(--muted)" }}>WS: {wsState}</span>
        <button onClick={() => stopMut.mutate()} disabled={stopMut.isPending}>
          ■ Stop
        </button>
      </div>
      {sess.github_warn_no_push && (
        <div className="msg warning">
          ⚠️ El PAT actual <strong>no tiene permisos de push</strong> sobre{" "}
          <code>{sess.project_slug}</code>. El agente podrá leer y trabajar en
          local, pero <code>git push</code> y abrir PR fallarán con 403 hasta
          que regeneres el token con scope adecuado en{" "}
          <a href="/github">/github/</a>.
        </div>
      )}

      <div className="session-layout">
        <section className="chat">
          <div ref={scrollerRef} className="scroller">
            {bubbles.length === 0 && (
              <p style={{ color: "var(--muted)", padding: "0.5rem" }}>Esperando eventos…</p>
            )}
            {bubbles.map((b) => (
              <BubbleView
                key={b.key}
                bubble={b}
                onResolvePerm={(id, answer) => resolvePerm.mutate({ id, answer })}
                resolving={resolvePerm.isPending}
              />
            ))}
          </div>
          <form
            className="input-bar"
            onSubmit={(e) => {
              e.preventDefault();
              const text = input.trim();
              if (!text) return;
              sendMut.mutate(text);
              setInput("");
            }}
          >
            <textarea
              value={input}
              onChange={(e) => setInput(e.target.value)}
              onKeyDown={sendOnEnter}
              placeholder="Mensaje al agente (Ctrl/Cmd+Enter)"
            />
            <button type="submit" className="primary" disabled={sendMut.isPending}>
              Enviar
            </button>
          </form>
        </section>

        <aside style={{ display: "grid", gap: "0.5rem" }}>
          <h3>Proyecto</h3>
          <p>
            <a href={`/projects/${sess.project_slug}`}>{sess.project}</a>
          </p>
          {/* Tabs: Archivos / Cambios / Rama. Estado controlado por useState. */}
          <div style={{ display: "flex", gap: "0.3rem", borderBottom: "1px solid var(--border)" }}>
            {(["archivos", "cambios", "rama"] as const).map((t) => (
              <button
                key={t}
                onClick={() => setTab(t)}
                style={{
                  border: "none",
                  background: "transparent",
                  padding: "0.3rem 0.6rem",
                  borderBottom: tab === t ? "2px solid var(--accent)" : "2px solid transparent",
                  color: tab === t ? "var(--accent)" : "var(--muted)",
                  fontWeight: tab === t ? 600 : 400,
                }}
              >
                {t.charAt(0).toUpperCase() + t.slice(1)}
              </button>
            ))}
          </div>
          <div style={{ minHeight: 200 }}>
            {tab === "archivos" && <ProjectTree slug={sess.project_slug} />}
            {tab === "cambios" && <ProjectDiff slug={sess.project_slug} />}
            {tab === "rama" && (
              <RamaTab slug={sess.project_slug} />
            )}
          </div>
        </aside>
      </div>
    </div>
  );
}

function BubbleView({
  bubble,
  onResolvePerm,
  resolving,
}: {
  bubble: Bubble;
  onResolvePerm: (id: string, answer: "allow" | "deny") => void;
  resolving: boolean;
}) {
  const ui = bubble.ui;
  if (!ui) return null;
  const p = ui.payload as Record<string, unknown>;
  switch (ui.kind) {
    case "agent_text":
      return (
        <div className="bubble agent-text">
          <span>{String(p.text ?? "")}</span>
          {p.streaming ? <span style={{ opacity: 0.5 }}>▍</span> : null}
          {p.from_user ? (
            <div style={{ opacity: 0.6, fontSize: "0.85em" }}>(usuario)</div>
          ) : null}
        </div>
      );
    case "agent_thinking":
      return (
        <details className="bubble agent-thinking">
          <summary>pensando…</summary>
          <pre>{String(p.text ?? "")}</pre>
        </details>
      );
    case "tool_call": {
      const awaiting = Boolean(p.awaiting_permission);
      const hasResult = Boolean(bubble.result);
      return (
        <div className="bubble tool-call">
          <div style={{ display: "flex", gap: "0.5rem", alignItems: "center", flexWrap: "wrap" }}>
            <code>{String(p.name ?? "")}</code>
            {awaiting && <span className="tag warn">esperando permiso</span>}
            {hasResult && <span style={{ opacity: 0.6, fontSize: "0.85em" }}>(resultado ↓)</span>}
          </div>
          <pre>{JSON.stringify(p.input ?? {}, null, 2)}</pre>
          {hasResult && bubble.result?.ui_event && (
            <ToolResultView ui={bubble.result.ui_event} />
          )}
        </div>
      );
    }
    case "permission_request": {
      const id = String(p.id ?? "");
      return (
        <div className="bubble permission-request">
          <div>
            <strong>{String(p.tool ?? "")}</strong>{" "}
            <span style={{ opacity: 0.6, fontSize: "0.85em" }}>· id={id.slice(0, 8)}</span>
          </div>
          <pre>{String(p.input_preview ?? "")}</pre>
          <div style={{ display: "flex", gap: "0.4rem" }}>
            <button
              className="primary"
              onClick={() => onResolvePerm(id, "allow")}
              disabled={resolving}
            >
              Permitir
            </button>
            <button
              className="danger"
              onClick={() => onResolvePerm(id, "deny")}
              disabled={resolving}
            >
              Denegar
            </button>
          </div>
        </div>
      );
    }
    case "run_result":
      return (
        <div className={`bubble run-result ${p.ok ? "" : "error"}`}>
          <strong>{p.ok ? "✓ Turno OK" : "✗ Turno con error"}</strong>{" "}
          <span style={{ opacity: 0.7 }}>
            ${Number(p.cost_usd ?? 0).toFixed(4)} · {String(p.num_turns ?? 0)} turnos
          </span>
          {p.summary ? <div style={{ marginTop: "0.3rem" }}>{String(p.summary)}</div> : null}
        </div>
      );
    case "session_status":
      return (
        <div className="bubble session-status">
          session_status: <code>{String(p.status ?? "")}</code>
          {p.model ? <> · model=<code>{String(p.model)}</code></> : null}
        </div>
      );
    case "git_branch":
      return (
        <div className="bubble git-branch">
          rama: <code>{String(p.branch ?? "")}</code>
          {p.dirty ? <span style={{ color: "var(--err-fg)" }}> ● dirty</span> : null}
        </div>
      );
    case "error":
      return (
        <div className="bubble error">{String(p.message ?? "error")}</div>
      );
    case "tool_result":
    case "permission_resolved":
      return null;
  }
}

function ToolResultView({ ui }: { ui: UIEvent }) {
  const p = ui.payload as { ok?: boolean; output?: unknown };
  const ok = Boolean(p.ok);
  return (
    <div className={`bubble tool-result ${ok ? "" : "error"}`} style={{ marginTop: "0.4rem" }}>
      <strong>{ok ? "OK" : "ERROR"}</strong>
      <pre>{typeof p.output === "string" ? p.output : JSON.stringify(p.output ?? "", null, 2)}</pre>
    </div>
  );
}

// Lee el estado git del proyecto (rama + dirty). Endpoint ligero: solo
// ejecuta `git rev-parse --abbrev-ref HEAD` y `git status --porcelain`.
// El watcher en vivo (FASE C.6) ya publica eventos git_branch; este
// componente es el snapshot al cargar la pestaña.
function RamaTab({ slug }: { slug: string }) {
  type BranchResp = { branch: string; dirty: boolean };
  const q = useQuery({
    queryKey: ["branch", slug],
    queryFn: async () => {
      // Reusamos el diff endpoint: si dirty, sabemos que hay cambios;
      // el branch lo leemos con un endpoint ligero. Como no tenemos
      // /api/v1/projects/<slug>/branch/ en backend, derivamos del diff.
      // El diff devuelve `path: null` cuando es global; si dirty=true,
      // asumimos rama actual. (Mejorable con un endpoint dedicado.)
      const d = await api<{ path: string | null; dirty: boolean }>(
        `/api/v1/projects/${slug}/diff/`,
      );
      return { branch: "(actual)", dirty: d.dirty } as BranchResp;
    },
    enabled: !!slug,
    refetchInterval: 5000,
  });
  if (q.isLoading) return <p className="meta">Leyendo estado git…</p>;
  if (q.error) return <p className="msg error">Error: {String(q.error)}</p>;
  const d = q.data;
  if (!d) return null;
  return (
    <div>
      <div>
        rama: <code>{d.branch}</code>{" "}
        {d.dirty
          ? <span style={{ color: "var(--err-fg)" }}>● dirty</span>
          : <span style={{ color: "var(--ok-fg)" }}>✓ clean</span>}
      </div>
      <p className="meta" style={{ marginTop: "0.4rem" }}>
        El watcher emite eventos <code>git_branch</code> en el chat en vivo
        cuando la rama o el dirty cambia.
      </p>
    </div>
  );
}
