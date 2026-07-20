// Vista Sesión (FASE C.4) — Layout OpenHands: chat izquierda, panel lateral derecho.
//
// Chat: discriminated union UIEvent v1 por kind (FASE B).
// Panel lateral: placeholders (FASE C.5 archivos+diff, C.6 rama).

import { useEffect, useRef, useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { api, ApiError } from "@/lib/api";
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
  // Para agrupación: el primer evento del grupo lleva la key del grupo;
  // los deltas subsiguientes actualizan el mismo Bubble.
  groupKey: string;
}

function useSession(id: string) {
  return useQuery({
    queryKey: ["session", id],
    queryFn: () => api<Session>(`/api/v1/sessions/${id}/`),
  });
}

/** Agrupa eventos del SDK en bubbles por bloque/turno.

  Reglas:
  - agent_text + stream.content_block_delta (text) → 1 bubble por bloque
    (el primer evento crea, los siguientes actualizan el mismo).
  - agent_thinking + system.thinking_tokens → 1 bubble por bloque.
  - tool_call → 1 bubble; tool_result del mismo tool_use_id → actualiza el
    mismo bubble.
  - run_result, error, session_status, git_branch → 1 bubble cada uno.
  - user (mensaje del operador) → 1 bubble por envío, agrupadas por turnId.
*/
function groupKey(ev: RawEventMessage, ui: UIEvent | null, turnId?: string): string {
  if (ui?.kind === "agent_text" || ui?.kind === "agent_thinking") {
    const p = ui.payload as { tool_use_id?: string };
    return `text:${p.tool_use_id || ev.seq}`; // fallback a seq si no hay tuid
  }
  if (ui?.kind === "tool_call" || ui?.kind === "tool_result") {
    const p = ui.payload as { tool_use_id?: string };
    return `tool:${p.tool_use_id || ev.seq}`;
  }
  if (ui?.kind === "permission_request") {
    return `perm:${(ui.payload as { id?: string }).id || ev.seq}`;
  }
  if (ui?.kind === "user") {
    return `user:${turnId || ev.seq}`;
  }
  return `evt:${ev.seq}`;
}

function ingestEvent(
  prev: Bubble[],
  msg: RawEventMessage,
  turnId?: string,
): Bubble[] {
  if (!msg.ui_event) return prev; // eventos sin UI (streaming crudo) se ignoran
  const ui = msg.ui_event;
  const key = groupKey(msg, ui, turnId);
  // tool_result: actualiza el tool_call existente en vez de crear uno nuevo
  if (ui.kind === "tool_result") {
    const idx = prev.findIndex((b) => b.groupKey === key && b.kind === "tool_call");
    if (idx >= 0) {
      const copy = prev.slice();
      copy[idx] = { ...copy[idx], result: msg, ui: { ...ui, kind: "tool_result" } };
      return copy;
    }
  }
  // tool_call con tool_result existente: actualiza
  if (ui.kind === "tool_call") {
    const idx = prev.findIndex((b) => b.groupKey === key);
    if (idx >= 0) {
      const copy = prev.slice();
      const existing = copy[idx];
      copy[idx] = {
        ...existing,
        raw: msg,
        ui,
        result: existing.result, // preserva el result si ya estaba
      };
      return copy;
    }
  }
  // agent_text o agent_thinking: actualiza el mismo grupo
  if (ui.kind === "agent_text" || ui.kind === "agent_thinking") {
    const idx = prev.findIndex((b) => b.groupKey === key);
    if (idx >= 0) {
      const copy = prev.slice();
      // Actualiza el payload con el último (texto acumula deltas)
      const merged: UIEvent = {
        ...ui,
        payload: { ...ui.payload, ...(prev[idx].ui?.payload ?? {}) },
        seq: msg.seq,
      };
      copy[idx] = { ...copy[idx], raw: msg, ui: merged };
      return copy;
    }
  }
  // Cualquier otro: append nuevo
  return [
    ...prev,
    { key, groupKey: key, kind: ui.kind, raw: msg, ui },
  ];
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
    // FASE UX.1: cargar el backlog ANTES de abrir el WS. Si el WS ya
    // envió los mismos eventos, dedup por seq los ignora.
    let cancelled = false;
    (async () => {
      try {
        const r = await api<RawEventMessage[]>(
          `/api/v1/sessions/${sid}/events/?limit=500`,
        );
        if (cancelled) return;
        setBubbles((prev) => {
          let next = prev;
          for (const ev of r) {
            if (seenSeq.current.has(ev.seq)) continue;
            seenSeq.current.add(ev.seq);
            next = ingestEvent(next, ev);
          }
          return next;
        });
      } catch (err) {
        console.error("[chat] backlog fetch failed", err);
      }
    })();
    const ws = openSessionWs(
      sid,
      {
        onEvent: (msg) => {
          if (seenSeq.current.has(msg.seq)) return;
          seenSeq.current.add(msg.seq);
          setBubbles((prev) => ingestEvent(prev, msg));
        },
        onStateChange: setWsState,
      },
      // last_seq = max seq visto (backlog + WS entrantes)
      // Para evitar duplicados, abrimos WS con last_seq = max(seenSeq) —
      // pero el backlog ya populó seenSeq, así que el WS nos manda solo
      // lo nuevo. Si el WS abre antes de que termine el fetch del backlog,
      // algunos eventos podrían llegar dos veces (dedup los ignora).
      seenSeq.current.size,
    );
    wsRef.current = ws;
    return () => {
      cancelled = true;
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
    // FASE UX.2: eco local. Al enviar, añadimos inmediatamente un bubble
    // "user" con el texto para que el usuario vea su mensaje sin esperar
    // a que el WS lo confirme. El WS después puede traer un evento user
    // duplicado — el dedup por seq lo maneja (y como el eco usa un turnId
    // sintético sin seq, no choca con el user del WS).
    onMutate: (text) => {
      const turnId = `local-${Date.now()}`;
      const stub: RawEventMessage = {
        seq: -Date.now(), // seq negativo = eco local (no choca con seq reales)
        type: "user",
        payload: { text },
        ui_event: {
          v: 1,
          seq: -1,
          session_id: sid,
          ts: new Date().toISOString(),
          kind: "user",
          payload: { text, from_user: true },
        },
        ts: new Date().toISOString(),
      };
      setBubbles((prev) => ingestEvent(prev, stub, turnId));
      // Auto-scroll al fondo al añadir.
      setTimeout(() => {
        const el = scrollerRef.current;
        if (el) el.scrollTop = el.scrollHeight;
      }, 0);
    },
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
      <div style={{ display: "flex", gap: "1rem", flexWrap: "wrap", marginBottom: "0.6rem", alignItems: "center" }}>
        <span>Proyecto: <strong>{sess.project}</strong></span>
        <ModelSelector slug={sess.project_slug} />
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
    case "user":
      return (
        <div className="bubble user">
          <span style={{ opacity: 0.6, fontSize: "0.85em" }}>tú:</span>{" "}
          <span>{String(p.text ?? "")}</span>
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


// ---------- FASE D.2: Selector de modelo en chat ----------
//
// Dropdown con los ModelProfile disponibles. Cambiar de modelo:
// 1. POST /api/v1/projects/<slug>/model/ {model_profile_id} → actualiza BD
// 2. Devuelve {needs_restart: true} → el banner "Reinicio requerido"
// 3. El usuario hace click en "Reiniciar" → POST /sessions/<id>/stop/
//    y luego navega a /projects/<slug>/start/ (legacy POST) que crea
//    nueva sesión con el nuevo modelo.
//
// En el MVP el "Reiniciar" navega a /projects/<slug>/ — la vista de
// proyectos (legacy) tiene el botón Start que crea la nueva sesión.

interface Model {
  id: number;
  name: string;
  provider: string;
  model: string;
  has_token: boolean;
}

interface Project {
  slug: string;
  model_profile: Model;
  model_profile_id: number;
}

function ModelSelector({ slug }: { slug: string }) {
  const modelsQ = useQuery({
    queryKey: ["models"],
    queryFn: () => api<Model[]>("/api/v1/models/"),
  });
  const projectQ = useQuery({
    queryKey: ["project", slug],
    queryFn: () => api<Project>(`/api/v1/projects/${slug}/`),
  });
  const qc = useQueryClient();
  const [msg, setMsg] = useState<{ kind: "ok" | "err"; text: string } | null>(null);

  const changeMut = useMutation({
    mutationFn: (modelProfileId: number) =>
      api<{ ok: boolean; old_model_profile: number; new_model_profile: number; needs_restart: boolean }>(
        `/api/v1/projects/${slug}/model/`,
        { method: "POST", body: { model_profile_id: modelProfileId } },
      ),
    onSuccess: (data) => {
      if (data.ok) {
        if (data.needs_restart) {
          setMsg({ kind: "ok", text: "Modelo cambiado — reinicia la sesión para aplicar." });
        } else {
          setMsg({ kind: "ok", text: "Modelo actualizado." });
        }
        void qc.invalidateQueries({ queryKey: ["project", slug] });
      } else {
        setMsg({ kind: "err", text: "Error cambiando modelo" });
      }
    },
    onError: (err: unknown) => {
      if (err instanceof ApiError) {
        const body = (err.body ?? {}) as { error?: string };
        setMsg({ kind: "err", text: body.error ?? `Error ${err.status}` });
      } else {
        setMsg({ kind: "err", text: String(err) });
      }
    },
  });

  if (modelsQ.isLoading || projectQ.isLoading) return <span className="meta">modelo…</span>;
  if (modelsQ.error || projectQ.error) return null;
  const models = modelsQ.data ?? [];
  const project = projectQ.data;
  if (!project || models.length === 0) return null;

  return (
    <div style={{ display: "flex", flexDirection: "column", gap: "0.2rem" }}>
      <label style={{ display: "flex", gap: "0.3rem", alignItems: "center" }}>
        <span className="meta">modelo:</span>
        <select
          value={project.model_profile_id}
          disabled={changeMut.isPending}
          onChange={(e) => {
            const newId = Number(e.target.value);
            if (newId !== project.model_profile_id) {
              changeMut.mutate(newId);
            }
          }}
          >
          {models.map((m) => (
            <option key={m.id} value={m.id}>
              {m.name} ({m.provider})
              {!m.has_token ? " — sin token" : ""}
            </option>
          ))}
        </select>
      </label>
      {msg && (
        <span className={`msg ${msg.kind === "ok" ? "info" : "error"}`} style={{ padding: "0.2rem 0.5rem" }}>
          {msg.text}
        </span>
      )}
    </div>
  );
}
