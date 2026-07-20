// Vista Sesión (FASE C.4) — Layout OpenHands: chat izquierda, panel lateral derecho.
//
// Chat: discriminated union UIEvent v1 por kind (FASE B).
// Panel lateral: placeholders (FASE C.5 archivos+diff, C.6 rama).
//
// FIX UX.6/UX.7/UX.8 (2026-07-20):
// - backlog via useQuery (clave estable, sin re-runs por refetch de sessQ)
// - eco local con key única de BubbleView (`bubble.user` en styles.css)
// - agrupado de deltas sin tool_use_id reusando la última key de stream
//   del mismo kind (vía streamState ref)

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
  groupKey: string;
}

// Estado de stream compartido: deltas consecutivos del mismo kind
// (agent_text o agent_thinking) sin tool_use_id se agrupan en una sola key.
interface StreamState {
  key: string | null;
  kind: UIEventKind | null;
}

function useSession(id: string) {
  return useQuery({
    queryKey: ["session", id],
    queryFn: () => api<Session>(`/api/v1/sessions/${id}/`),
  });
}

function useSessionEvents(sid: string) {
  return useQuery({
    queryKey: ["session-events", sid],
    queryFn: () =>
      api<RawEventMessage[]>(`/api/v1/sessions/${sid}/events/?limit=500`),
    enabled: !!sid,
    staleTime: Infinity, // el WS entrega lo nuevo, no necesitamos refetch
  });
}

/** Genera la groupKey de un evento.
  - agent_text + tool_use_id → text:<tuid> (1 bubble por bloque macro)
  - agent_text sin tool_use_id (delta de stream) → reusa la última key
    de stream del mismo kind (vía streamState)
  - tool_call/tool_result → tool:<tuid>
  - permission_request → perm:<id>
  - user → user:<turnId|seq>
  - resto → evt:<seq>
*/
function computeGroupKey(
  ev: RawEventMessage,
  ui: UIEvent,
  turnId: string | undefined,
  stream: StreamState,
): { key: string; nextStream: StreamState } {
  const p = ui.payload as { tool_use_id?: string; id?: string };
  if (ui.kind === "agent_text" || ui.kind === "agent_thinking") {
    if (p.tool_use_id) {
      const k = `text:${p.tool_use_id}`;
      return { key: k, nextStream: { key: null, kind: null } };
    }
    if (stream.kind === ui.kind && stream.key) {
      return { key: stream.key, nextStream: stream };
    }
    const k = `stream:${ui.kind}:${ev.seq}`;
    return { key: k, nextStream: { key: k, kind: ui.kind } };
  }
  if (ui.kind === "tool_call" || ui.kind === "tool_result") {
    const k = `tool:${p.tool_use_id || ev.seq}`;
    return { key: k, nextStream: { key: null, kind: null } };
  }
  if (ui.kind === "permission_request") {
    return { key: `perm:${p.id || ev.seq}`, nextStream: { key: null, kind: null } };
  }
  if (ui.kind === "user") {
    return { key: `user:${turnId || ev.seq}`, nextStream: { key: null, kind: null } };
  }
  return { key: `evt:${ev.seq}`, nextStream: { key: null, kind: null } };
}

function ingestEvent(
  prev: Bubble[],
  msg: RawEventMessage,
  stream: StreamState,
  turnId?: string,
): { next: Bubble[]; stream: StreamState } {
  if (!msg.ui_event) return { next: prev, stream };
  const ui = msg.ui_event;
  const { key, nextStream } = computeGroupKey(msg, ui, turnId, stream);
  // tool_result → actualiza el tool_call existente
  if (ui.kind === "tool_result") {
    const idx = prev.findIndex((b) => b.groupKey === key && b.kind === "tool_call");
    if (idx >= 0) {
      const copy = prev.slice();
      copy[idx] = { ...copy[idx], result: msg, ui: { ...ui, kind: "tool_result" } };
      return { next: copy, stream: nextStream };
    }
    // tool_call aún no ha llegado → crear bubble tool_result igual
    return {
      next: [...prev, { key, groupKey: key, kind: ui.kind, raw: msg, ui }],
      stream: nextStream,
    };
  }
  // tool_call existente: actualiza raw/ui preservando result
  if (ui.kind === "tool_call") {
    const idx = prev.findIndex((b) => b.groupKey === key);
    if (idx >= 0) {
      const copy = prev.slice();
      const existing = copy[idx];
      copy[idx] = {
        ...existing,
        raw: msg,
        ui,
        result: existing.result,
      };
      return { next: copy, stream: nextStream };
    }
  }
  // agent_text/agent_thinking en mismo grupo → merge por acumulación de texto
  if (ui.kind === "agent_text" || ui.kind === "agent_thinking") {
    const idx = prev.findIndex((b) => b.groupKey === key);
    if (idx >= 0) {
      const copy = prev.slice();
      const prevUi = prev[idx].ui;
      const prevText =
        prevUi && (prevUi.kind === "agent_text" || prevUi.kind === "agent_thinking")
          ? String((prevUi.payload as { text?: string }).text ?? "")
          : "";
      const newText = String((ui.payload as { text?: string }).text ?? "");
      const mergedPayload = { ...ui.payload, text: prevText + newText };
      const merged: UIEvent = { ...ui, payload: mergedPayload, seq: msg.seq };
      copy[idx] = { ...copy[idx], raw: msg, ui: merged };
      return { next: copy, stream: nextStream };
    }
  }
  // Cualquier otro: append nuevo
  return {
    next: [...prev, { key, groupKey: key, kind: ui.kind, raw: msg, ui }],
    stream: nextStream,
  };
}

function useSessionIdFromPath(): string {
  const path = window.location.pathname;
  const m = path.match(/^\/sessions\/([0-9a-f-]{36})/);
  return m && m[1] ? m[1] : "";
}

export function SessionPage() {
  const sid = useSessionIdFromPath();
  const sessQ = useSession(sid);
  const eventsQ = useSessionEvents(sid);
  const [bubbles, setBubbles] = useState<Bubble[]>([]);
  const [wsState, setWsState] = useState<string>("connecting");
  const [input, setInput] = useState("");
  const [tab, setTab] = useState<"archivos" | "cambios" | "rama">("archivos");
  const wsRef = useRef<ReturnType<typeof openSessionWs> | null>(null);
  const seenSeq = useRef<Set<number>>(new Set());
  const streamRef = useRef<StreamState>({ key: null, kind: null });
  const scrollerRef = useRef<HTMLDivElement | null>(null);
  const bubblesRef = useRef<Bubble[]>([]);

  // Mantener ref sincronizada con bubbles (para usar dentro del WS onEvent
  // sin provocar re-renders innecesarios).
  useEffect(() => {
    bubblesRef.current = bubbles;
  }, [bubbles]);

  // FIX UX.6: backlog via useQuery con clave estable.
  // Se sincroniza UNA VEZ al montar (o al cambiar de sid), no en cada
  // re-render. El WS entrega los eventos nuevos.
  useEffect(() => {
    if (!sid) return;
    seenSeq.current.clear();
    streamRef.current = { key: null, kind: null };
    setBubbles([]);
    if (eventsQ.data) {
      const r = eventsQ.data;
      const seen = new Set<number>();
      let stream: StreamState = { key: null, kind: null };
      let next: Bubble[] = [];
      for (const ev of r) {
        if (seen.has(ev.seq)) continue;
        seen.add(ev.seq);
        seenSeq.current.add(ev.seq);
        const out = ingestEvent(next, ev, stream);
        next = out.next;
        stream = out.stream;
      }
      streamRef.current = stream;
      setBubbles(next);
    }
    // Solo al montar o cambiar de sid; el refetch de useQuery
    // actualiza `eventsQ.data` pero solo aplicamos si `seenSeq.current.size === 0`
    // (es decir, primera carga).
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [sid, eventsQ.dataUpdatedAt === 0]);

  // WS: abre siempre, dedupe por seenSeq, suma al state existente.
  useEffect(() => {
    if (!sid || !sessQ.data) return;
    const ws = openSessionWs(
      sid,
      {
        onEvent: (msg) => {
          if (seenSeq.current.has(msg.seq)) return;
          seenSeq.current.add(msg.seq);
          setBubbles((prev) => {
            const out = ingestEvent(prev, msg, streamRef.current);
            streamRef.current = out.stream;
            return out.next;
          });
        },
        onStateChange: setWsState,
      },
      // last_seq = max visto (0 al inicio = WS envía todo y dedup filtra)
      seenSeq.current.size === 0
        ? 0
        : Math.max(...Array.from(seenSeq.current)),
    );
    wsRef.current = ws;
    return () => {
      ws.close();
      wsRef.current = null;
    };
  }, [sid, sessQ.data]);

  // Auto-scroll al fondo al cambiar el número de bubbles.
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
    // FIX UX.2/UX.7: eco local con seq negativo y groupKey estable
    // (user:<turnId>) — siempre crea un Bubble único visible.
    onMutate: (text) => {
      const turnId = `local-${Date.now()}`;
      const stub: RawEventMessage = {
        seq: -Date.now(),
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
      setBubbles((prev) => {
        const out = ingestEvent(prev, stub, streamRef.current, turnId);
        streamRef.current = out.stream;
        return out.next;
      });
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
          <span style={{ border: "1px solid var(--border)", padding: "0 0 0.4rem", borderRadius: 4 }}>
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
    case "agent_text": {
      const text = String(p.text ?? "");
      const streaming = Boolean(p.streaming);
      return (
        <div className="bubble agent-text">
          <span style={{ whiteSpace: "pre-wrap" }}>{text}</span>
          {streaming && !text.endsWith("▍") ? <span style={{ opacity: 0.5 }}>▍</span> : null}
        </div>
      );
    }
    case "agent_thinking":
      return (
        <details className="bubble agent-thinking">
          <summary>pensando…</summary>
          <pre style={{ whiteSpace: "pre-wrap" }}>{String(p.text ?? "")}</pre>
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
    case "user":
      return (
        <div className="bubble user">
          <span style={{ opacity: 0.6, fontSize: "0.85em" }}>tú:</span>{" "}
          <span style={{ whiteSpace: "pre-wrap" }}>{String(p.text ?? "")}</span>
        </div>
      );
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
      <pre style={{ maxHeight: 200, overflow: "auto" }}>
        {typeof p.output === "string" ? p.output : JSON.stringify(p.output ?? null, null, 2)}
      </pre>
    </div>
  );
}

// Selector de modelo (FASE D.2) y RamaTab (FASE C.6) — definidos abajo.

function RamaTab({ slug }: { slug: string }) {
  // Placeholder simple; FASE C.6 mostrará la rama actual y cambios.
  const { data } = useQuery({
    queryKey: ["git-branch", slug],
    queryFn: () => api<{ branch: string; dirty: boolean }>(`/api/v1/projects/${slug}/git/`),
    refetchInterval: 5_000,
  });
  return (
    <div>
      <p>
        Rama: <code>{data?.branch ?? "?"}</code>
        {data?.dirty ? <span style={{ color: "var(--err-fg)" }}> ● dirty</span> : null}
      </p>
    </div>
  );
}

// Selector de modelo (FASE D.2)
function ModelSelector({ slug }: { slug: string }) {
  const { data: profiles } = useQuery({
    queryKey: ["models"],
    queryFn: () => api<Array<{ id: number; name: string; provider: string }>>(`/api/v1/models/`),
  });
  const { data: project } = useQuery({
    queryKey: ["project", slug],
    queryFn: () => api<{ model_profile: number | null; needs_restart?: boolean }>(`/api/v1/projects/${slug}/`),
  });
  const [msg, setMsg] = useState<string | null>(null);
  const qc = useQueryClient();
  const mut = useMutation({
    mutationFn: (modelId: number) =>
      api(`/api/v1/projects/${slug}/model/`, {
        method: "POST",
        body: { model_id: modelId },
      }),
    onSuccess: (data) => {
      const d = data as { needs_restart?: boolean };
      setMsg(d.needs_restart ? "Modelo cambiado — reinicia la sesión para aplicar" : "Modelo actualizado");
      void qc.invalidateQueries({ queryKey: ["project", slug] });
    },
    onError: (e) => setMsg(String(e)),
  });
  if (!profiles) return null;
  const current = project?.model_profile;
  return (
    <span>
      <span style={{ marginRight: "0.3rem" }}>modelo:</span>
      <select
        value={current ?? ""}
        onChange={(e) => {
          const v = Number(e.target.value);
          if (!Number.isNaN(v)) mut.mutate(v);
        }}
        disabled={mut.isPending}
      >
        {profiles.map((p) => (
          <option key={p.id} value={p.id}>
            {p.name} ({p.provider})
          </option>
        ))}
      </select>
      {msg && (
        <span style={{ marginLeft: "0.5rem", color: "var(--muted)", fontSize: "0.85em" }}>{msg}</span>
      )}
    </span>
  );
}
