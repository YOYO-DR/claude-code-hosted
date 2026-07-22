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

import { useEffect, useMemo, useRef, useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { api } from "@/lib/api";
import { openSessionWs, type RawEventMessage } from "@/lib/ws";
import type { UIEvent, UIEventKind, SlashCommand } from "@/types/uievents";
import { ProjectTree, ProjectDiff } from "@/components/ProjectTree";
import { Modal } from "@/components/Modal";

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
  /** SP9.2: si el permiso ya está resuelto (vía web o Telegram),
   * deshabilitamos los botones y mostramos el outcome en su lugar. */
  resolved?: { outcome: string; source: string };
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
  // tool_result → adjunta el resultado al tool_call existente SIN pisar su
  // `ui` (antes se ponía ui.kind="tool_result", y como BubbleView devuelve
  // null para tool_result, el bubble del tool_call desaparecía al llegar su
  // resultado — no se veía la llamada. Ahora se conserva el tool_call y el
  // resultado se muestra anidado vía ToolResultView).
  if (ui.kind === "tool_result") {
    const idx = prev.findIndex((b) => b.groupKey === key && b.kind === "tool_call");
    if (idx >= 0) {
      const copy = prev.slice();
      copy[idx] = { ...copy[idx], result: msg };
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
  // agent_text/agent_thinking en mismo grupo → reemplazar (cada delta
  // del WS trae el buffer acumulado del SDK, no un suffix incremental).
  if (ui.kind === "agent_text" || ui.kind === "agent_thinking") {
    const idx = prev.findIndex((b) => b.groupKey === key);
    if (idx >= 0) {
      const copy = prev.slice();
      copy[idx] = { ...copy[idx], raw: msg, ui };
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
  const [restartOpen, setRestartOpen] = useState(false);
  const wsRef = useRef<ReturnType<typeof openSessionWs> | null>(null);
  const seenSeq = useRef<Set<number>>(new Set());
  const streamRef = useRef<StreamState>({ key: null, kind: null });
  const scrollerRef = useRef<HTMLDivElement | null>(null);
  const bubblesRef = useRef<Bubble[]>([]);

  // SP2: reintento automático del session-detail mientras siga en STARTING
  // para detectar el flip a RUNNING/IDLE sin esperar al WS. Backoff: 2s.
  useEffect(() => {
    const status = sessQ.data?.status;
    if (status !== "starting") return;
    const t = setInterval(() => {
      sessQ.refetch();
    }, 2000);
    return () => clearInterval(t);
  }, [sessQ.data?.status, sessQ]);

  // SP2: extrae el último paso de boot emitido por el panel/worker para
  // mostrar timeline y deshabilitar el input hasta que el worker esté listo.
  const bootStep = useMemo(() => {
    // 1) Cualquier session_status cuyo status NO sea 'running' ni 'idle'
    //    se considera un paso de boot (panel: session.created / worker.scheduled;
    //    worker: 'init', etc.).
    // 2) session_status con status='running' o 'idle' => listo.
    // 3) El primer run_result también cuenta como listo (turno terminó).
    let latestBoot: string | null = null;
    let ready = false;
    for (const ev of eventsQ.data ?? []) {
      const t = ev.type;
      const payload = (ev.ui_event?.payload ?? ev.payload ?? {}) as Record<string, unknown>;
      if (t === "session_status" || t === "session_step") {
        const s = String(payload.status ?? "");
        if (s === "running" || s === "idle") { ready = true; continue; }
        if (s === "starting") continue;
        latestBoot = String(payload.message ?? s);
        // SP6: un paso de boot POSTERIOR invalida el "listo" de la corrida
        // anterior. Sin esto, tras un ↻ Reiniciar la sesión seguiría
        // pareciendo lista por los eventos viejos y el input dejaría mandar
        // mensajes a un worker que aún arranca (409).
        ready = false;
      }
      if (t === "result" || t === "run_result") ready = true;
      if (t === "agent_text" || t === "user" || t === "tool_call") ready = true;
    }
    return { latestBoot, ready };
  }, [eventsQ.data]);

  // SP11: context_usage es UIEvent efímero (no se persiste en BD). Solo
  // llega por WS vía _redis_publish_ui. Lo capturamos en un state efímero
  // y lo renderizamos como una barra debajo del input-bar, NO como bubble
  // (no compite por espacio con la conversación).
  const [ctxLive, setCtxLive] = useState<{
    total: number; max: number; pct: number; model: string;
    threshold: number | null; enabled: boolean;
  } | null>(null);
  const ctx = ctxLive;

  // SP12: comandos `/` disponibles (efímero, del init del worker). Toggle de
  // verbosidad para ocultar eventos verbosos (hooks / desconocidos).
  const [slashCmds, setSlashCmds] = useState<SlashCommand[]>([]);
  const [slashIdx, setSlashIdx] = useState(0);
  const [showDetails, setShowDetails] = useState(false);

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
          const kind = msg.ui_event?.kind;
          // SP11/SP12: efímeros no-burbuja (seq=0 fijo). Se manejan ANTES del
          // dedup por seq: si pasaran por seenSeq, solo el primero se
          // procesaría y el resto (refresh de la barra, actualización de
          // comandos) se perderían silenciosamente.
          if (kind === "context_usage") {
            const p = msg.ui_event!.payload as {
              total_tokens?: number; max_tokens?: number; percentage?: number;
              model?: string; auto_compact_threshold?: number | null; auto_compact_enabled?: boolean;
            };
            setCtxLive({
              total: Number(p.total_tokens ?? 0),
              max: Number(p.max_tokens ?? 0),
              pct: Number(p.percentage ?? 0),
              model: String(p.model ?? ""),
              threshold: p.auto_compact_threshold == null ? null : Number(p.auto_compact_threshold),
              enabled: Boolean(p.auto_compact_enabled),
            });
            return;
          }
          if (kind === "slash_commands") {
            const p = msg.ui_event!.payload as { commands?: SlashCommand[] };
            setSlashCmds(Array.isArray(p.commands) ? p.commands : []);
            return;
          }
          if (seenSeq.current.has(msg.seq)) return;
          seenSeq.current.add(msg.seq);
          setBubbles((prev) => {
            const out = ingestEvent(prev, msg, streamRef.current);
            streamRef.current = out.stream;
            return out.next;
          });
        },
        // SP9.2: el perm_resolved sidecar llega por WS — mapea al bubble
        // pendiente por su id de perm request y lo marca como resuelto (los
        // botones se deshabilitan y aparece el desenlace, venga de web o
        // Telegram, indistinguible al usuario).
        onPermResolved: ({ id, outcome, source }) => {
          setBubbles((prev) => {
            const idx = prev.findIndex(
              (b) => b.kind === "permission_request" &&
                String((b.ui?.payload as { id?: string })?.id ?? "") === id,
            );
            if (idx < 0) return prev;
            const copy = prev.slice();
            copy[idx] = { ...copy[idx], resolved: { outcome, source } };
            return copy;
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

  // SP12: auto-scroll suave. Solo "pega" al fondo si el usuario ya estaba
  // cerca (si scrolleó arriba a leer, no le secuestramos la vista). Suave al
  // aparecer un bubble nuevo; instantáneo mientras un bubble streamea (evita
  // jank de animaciones encoladas por cada token).
  const stickRef = useRef(true);
  const lastLenRef = useRef(0);
  const onScrollerScroll = () => {
    const el = scrollerRef.current;
    if (!el) return;
    stickRef.current = el.scrollHeight - el.scrollTop - el.clientHeight < 80;
  };
  useEffect(() => {
    const el = scrollerRef.current;
    if (!el || !stickRef.current) return;
    const grew = bubbles.length !== lastLenRef.current;
    lastLenRef.current = bubbles.length;
    el.scrollTo({ top: el.scrollHeight, behavior: grew ? "smooth" : "auto" });
  }, [bubbles]);

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
      stickRef.current = true; // al enviar, siempre seguimos al fondo
      setBubbles((prev) => {
        const out = ingestEvent(prev, stub, streamRef.current, turnId);
        streamRef.current = out.stream;
        return out.next;
      });
    },
    onSuccess: () => qc.invalidateQueries({ queryKey: ["session", sid] }),
  });
  const stopMut = useMutation({
    mutationFn: () => api(`/api/v1/sessions/${sid}/stop/`, { method: "POST" }),
    onSuccess: () => qc.invalidateQueries({ queryKey: ["session", sid] }),
  });
  const resolvePerm = useMutation({
    mutationFn: ({ id, answer, option_index }: {
      id: string;
      answer: "allow" | "allow_always" | "deny";
      option_index?: number;
    }) =>
      api(`/api/v1/permissions/${id}/resolve/`, {
        method: "POST",
        body: option_index !== undefined
          ? { answer, option_index }
          : { answer },
      }),
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

  // SP12: menú `/`. Se abre mientras el input empieza por `/` y aún no hay
  // espacio (se está tecleando el nombre del comando). Fuente: la lista que
  // emitió el worker (get_server_info). "Ejecutar al elegir": enviar el comando.
  const slashActive = input.startsWith("/") && !input.slice(1).includes(" ");
  const slashQuery = slashActive ? input.slice(1).toLowerCase() : "";
  const slashMatches = slashActive
    ? slashCmds.filter((c) => c.name.toLowerCase().includes(slashQuery)).slice(0, 8)
    : [];
  const slashOpen = slashMatches.length > 0;
  const slashSel = Math.min(slashIdx, slashMatches.length - 1);

  const selectSlash = (cmd: SlashCommand) => {
    sendMut.mutate(`/${cmd.name}`);
    setInput("");
    setSlashIdx(0);
  };

  const onInputKeyDown: React.KeyboardEventHandler<HTMLTextAreaElement> = (e) => {
    if (slashOpen) {
      if (e.key === "ArrowDown") {
        e.preventDefault();
        setSlashIdx((i) => (Math.min(i, slashMatches.length - 1) + 1) % slashMatches.length);
        return;
      }
      if (e.key === "ArrowUp") {
        e.preventDefault();
        setSlashIdx((i) => (Math.min(i, slashMatches.length - 1) - 1 + slashMatches.length) % slashMatches.length);
        return;
      }
      if (e.key === "Enter" || e.key === "Tab") {
        e.preventDefault();
        const pick = slashMatches[slashSel];
        if (pick) selectSlash(pick);
        return;
      }
      if (e.key === "Escape") {
        e.preventDefault();
        setInput("");
        return;
      }
    }
    sendOnEnter(e);
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
        <button onClick={() => setRestartOpen(true)} title="Aplicar cambios de modelo o MCP">
          ↻ Reiniciar
        </button>
      </div>
      {restartOpen && (
        <RestartSessionModal sid={sid} onClose={() => setRestartOpen(false)} />
      )}
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
          <div className="chat-toolbar">
            <label className="details-toggle" title="Muestra eventos verbosos (hooks, telemetría, tipos no reconocidos)">
              <input
                type="checkbox"
                checked={showDetails}
                onChange={(e) => setShowDetails(e.target.checked)}
                data-testid="details-toggle"
              />
              mostrar detalles
            </label>
          </div>
          <div ref={scrollerRef} className="scroller" onScroll={onScrollerScroll}>
            {bubbles.length === 0 && (
              <p style={{ color: "var(--muted)", padding: "0.5rem" }}>Esperando eventos…</p>
            )}
            {bubbles.map((b) => (
              <BubbleView
                key={b.key}
                bubble={b}
                showDetails={showDetails}
                onResolvePerm={(id, answer, option_index) =>
                resolvePerm.mutate({ id, answer, option_index })
              }
                resolving={resolvePerm.isPending}
              />
            ))}
          </div>
          {/* SP2: banner de progreso mientras el worker arranca. El input se
              deshabilita hasta que el worker haya emitido su primer evento
              real (session_status=running o primer agent_text). Evita que el
              usuario mande mensajes a un worker que aún no existe. */}
          {sess.status === "starting" && !bootStep.ready && (
            <div className="boot-banner" data-testid="boot-banner">
              <span className="boot-spinner" aria-hidden="true">⏳</span>
              <span>
                {bootStep.latestBoot ?? "Programando worker…"}
              </span>
            </div>
          )}
          {/* SP12: menú `/` — comandos reales del worker; se ejecuta al elegir. */}
          {slashOpen && (
            <div className="slash-menu" data-testid="slash-menu">
              {slashMatches.map((c, i) => (
                <button
                  type="button"
                  key={c.name}
                  className={`slash-item${i === slashSel ? " active" : ""}`}
                  onMouseEnter={() => setSlashIdx(i)}
                  onClick={() => selectSlash(c)}
                >
                  <code>/{c.name}</code>
                  {c.description && <span className="slash-desc">{c.description}</span>}
                </button>
              ))}
            </div>
          )}
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
              onKeyDown={onInputKeyDown}
              placeholder={
                sess.status === "starting" && !bootStep.ready
                  ? "Esperando que el worker esté listo…"
                  : "Mensaje al agente (Ctrl/Cmd+Enter)"
              }
              disabled={sess.status === "starting" && !bootStep.ready}
              data-testid="chat-input"
            />
            <button
              type="submit"
              className="primary"
              disabled={sendMut.isPending || (sess.status === "starting" && !bootStep.ready)}
              data-testid="chat-send"
            >
              Enviar
            </button>
          </form>
          {/* SP11: indicador de contexto debajo del input-bar. Efímero:
              lo emite el worker cada ~10s via WS; si no llega nada, no se
              dibuja. No compite con la conversación por espacio. */}
          {ctx && ctx.max > 0 && (
            <div className="ctx-bar" data-testid="ctx-bar" title={ctx.model}>
              <span className="ctx-bar-label">contexto</span>
              <div className="ctx-bar-track">
                <div
                  className="ctx-bar-fill"
                  style={{
                    width: `${Math.min(100, Math.max(0, ctx.pct))}%`,
                    background: ctx.pct >= 90 ? "var(--err-fg)" : ctx.pct >= 70 ? "#e3b341" : "var(--accent)",
                  }}
                />
                {/* SP12: marcador del umbral de auto-compact. */}
                {ctx.threshold != null && ctx.threshold > 0 && ctx.threshold < 100 && (
                  <div
                    className="ctx-bar-threshold"
                    style={{ left: `${ctx.threshold}%` }}
                    title={`auto-compact ~${ctx.threshold}%${ctx.enabled ? "" : " (deshabilitado)"}`}
                  />
                )}
              </div>
              <span className="ctx-bar-numbers">
                {(ctx.total / 1000).toFixed(1)}k / {(ctx.max / 1000).toFixed(0)}k ({ctx.pct.toFixed(0)}%)
                {ctx.threshold != null && ctx.threshold > 0 ? (
                  <span className="ctx-bar-threshold-num" title="umbral de auto-compact">
                    {" "}⚡{ctx.threshold}%
                  </span>
                ) : null}
              </span>
            </div>
          )}
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
          <div
            className="sidebar-tab-content"
            style={{ minHeight: 200 }}
          >
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
  showDetails,
  onResolvePerm,
  resolving,
}: {
  bubble: Bubble;
  showDetails: boolean;
  onResolvePerm: (
    id: string,
    answer: "allow" | "allow_always" | "deny",
    option_index?: number,
  ) => void;
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
          {streaming && !text.endsWith("▍") ? <span className="stream-cursor">▍</span> : null}
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
      const serverTool = p.server_tool ? String(p.server_tool) : "";
      return (
        <div className="bubble tool-call">
          <div style={{ display: "flex", gap: "0.5rem", alignItems: "center", flexWrap: "wrap" }}>
            {serverTool && <span title={`server tool: ${serverTool}`}>🌐</span>}
            <code>{String(p.name ?? "")}</code>
            {serverTool && <span className="tag">server</span>}
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
      const r = bubble.resolved;
      const outcomeTag =
        r?.outcome === "allow" || r?.outcome === "allow_always"
          ? `✓ ${r.outcome === "allow_always" ? "Permitido siempre" : "Permitido"}${r.source ? ` · ${r.source}` : ""}`
          : r?.outcome === "deny"
            ? `✗ Denegado${r.source ? ` · ${r.source}` : ""}`
            : r?.outcome === "timeout"
              ? "⏱ Expirado sin respuesta"
              : null;
      // SP9.1: las preguntas del agente (AskUserQuestion) llegan como tool_name
      // "AskUserQuestion" con input estructurado (question + options).
      // Renderizamos cada opción como botón cliqueable. La elegida viaja al
      // worker vía option_index → updated_input.
      const isQuestion = String(p.tool ?? "") === "AskUserQuestion";
      const inputFull = (p.input_full ?? {}) as {
        question?: string;
        options?: Array<{ label: string; description?: string; preview?: string }>;
        header?: string;
      };
      const options = isQuestion && Array.isArray(inputFull.options)
        ? inputFull.options
        : [];
      return (
        <div className="bubble permission-request">
          <div>
            <strong>{String(p.tool ?? "")}</strong>{" "}
            <span style={{ opacity: 0.6, fontSize: "0.85em" }}>· id={id.slice(0, 8)}</span>
          </div>
          <pre>{String(p.input_preview ?? "")}</pre>
          {outcomeTag ? (
            <div className="tag ok" style={{ marginTop: "0.3rem" }}>{outcomeTag}</div>
          ) : (
            <div style={{ display: "flex", gap: "0.4rem", flexWrap: "wrap" }}>
              {!isQuestion && (
                <>
                  <button
                    className="primary"
                    disabled={resolving}
                    onClick={() => onResolvePerm(id, "allow")}
                  >
                    Permitir
                  </button>
                  <button
                    disabled={resolving}
                    onClick={() => onResolvePerm(id, "allow_always")}
                  >
                    Permitir siempre
                  </button>
                  <button
                    className="danger"
                    disabled={resolving}
                    onClick={() => onResolvePerm(id, "deny")}
                  >
                    Denegar
                  </button>
                </>
              )}
              {isQuestion && options.length > 0 && options.map((o, i) => (
                <button
                  key={i}
                  className="primary"
                  disabled={resolving}
                  onClick={() => onResolvePerm(id, "allow", i)}
                  title={o.description || o.label}
                >
                  {o.label}
                </button>
              ))}
              {isQuestion && (
                <button
                  className="danger"
                  disabled={resolving}
                  onClick={() => onResolvePerm(id, "deny")}
                >
                  Denegar
                </button>
              )}
            </div>
          )}
          {isQuestion && inputFull.question && (
            <p style={{ margin: "0.3rem 0 0", fontStyle: "italic" }}>
              {inputFull.question}
            </p>
          )}
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
          {p.message ? (
            <span>{String(p.message)}</span>
          ) : (
            <>session_status: <code>{String(p.status ?? "")}</code></>
          )}
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
    case "compact": {
      const pre = p.pre_tokens ? Number(p.pre_tokens) : null;
      return (
        <div className="bubble compact" data-testid="bubble-compact">
          <span>✂ contexto compactado</span>
          {pre ? <span style={{ opacity: 0.7 }}> · antes {(pre / 1000).toFixed(0)}k tokens</span> : null}
          {p.trigger ? <span style={{ opacity: 0.7 }}> · {String(p.trigger)}</span> : null}
        </div>
      );
    }
    case "rate_limit": {
      const util = p.utilization != null ? Number(p.utilization) : null;
      return (
        <div className="bubble rate-limit" data-testid="bubble-rate-limit">
          <strong>⏳ límite de tasa</strong>{" "}
          {p.status ? <span>· {String(p.status)}</span> : null}
          {util != null ? <span> · uso {(util * 100).toFixed(0)}%</span> : null}
          {p.resets_at ? <span style={{ opacity: 0.7 }}> · resetea {String(p.resets_at)}</span> : null}
        </div>
      );
    }
    case "task": {
      const desc = String(p.description ?? p.summary ?? "");
      const status = String(p.status ?? p.subtype ?? "");
      return (
        <div className="bubble task" data-testid="bubble-task">
          <span>◈ subagente</span>
          {status ? <span className="tag">{status}</span> : null}
          {desc ? <span style={{ opacity: 0.85 }}> {desc}</span> : null}
          {p.last_tool_name ? (
            <span style={{ opacity: 0.6, fontSize: "0.85em" }}> · {String(p.last_tool_name)}</span>
          ) : null}
        </div>
      );
    }
    case "hook": {
      // Verboso: solo con el toggle "mostrar detalles".
      if (!showDetails) return null;
      return (
        <details className="bubble hook">
          <summary>hook: {String(p.hook_event_name ?? p.subtype ?? "")}</summary>
          <pre style={{ whiteSpace: "pre-wrap" }}>{JSON.stringify(p.data ?? {}, null, 2)}</pre>
        </details>
      );
    }
    case "tool_result":
    case "permission_resolved":
    case "context_usage":
    case "slash_commands":
      return null;
    default: {
      // Catch-all: ningún tipo del SDK desaparece en silencio. Verboso →
      // solo con el toggle. Muestra el JSON crudo del UIEvent.
      if (!showDetails) return null;
      return (
        <details className="bubble unknown" data-testid="bubble-unknown">
          <summary>evento SDK: {String(ui.kind)}</summary>
          <pre style={{ whiteSpace: "pre-wrap" }}>{JSON.stringify(p ?? {}, null, 2)}</pre>
        </details>
      );
    }
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

// SP6: reinicio de sesión. El modelo y los MCP no recargan en caliente
// (§4.3), así que aplicar un cambio pasa por rearrancar el worker. Dos
// semánticas distintas, por eso el modal en vez de un botón directo:
//   - resume: misma fila. El chat conserva su historia y el agente su
//     contexto (el worker arranca con el `resume` del SDK sobre el
//     sdk_session_id guardado).
//   - new: sesión nueva. Chat vacío y agente sin contexto.
function RestartSessionModal({ sid, onClose }: { sid: string; onClose: () => void }) {
  const qc = useQueryClient();
  const [busy, setBusy] = useState<"resume" | "new" | null>(null);
  const [err, setErr] = useState<string | null>(null);

  const run = async (mode: "resume" | "new") => {
    setErr(null);
    setBusy(mode);
    try {
      const r = await api<{ ok: boolean; mode: string; id: string; resumed?: boolean }>(
        `/api/v1/sessions/${sid}/restart/`,
        { method: "POST", body: { mode } },
      );
      if (r.mode === "new") {
        window.location.href = `/sessions/${r.id}`;
        return;
      }
      // Refrescar la sesión (status → starting, lo que rearma el polling y
      // el banner de boot) y el backlog (trae el paso session.restarting).
      await qc.invalidateQueries({ queryKey: ["session", sid] });
      await qc.invalidateQueries({ queryKey: ["session-events", sid] });
      onClose();
    } catch (e: unknown) {
      const m = (() => {
        try { return JSON.parse(String(e)).error ?? String(e); }
        catch { return String(e); }
      })();
      setErr(m);
      setBusy(null);
    }
  };

  return (
    <Modal open variant="custom" title="Reiniciar sesión" onCancel={onClose}>
      <p style={{ marginTop: 0, color: "var(--muted)" }}>
        El modelo y los MCP no se recargan en caliente: hay que rearrancar el
        worker para que el cambio tenga efecto.
      </p>
      <div className="restart-options">
        <div className="restart-option">
          <strong>Continuar esta conversación</strong>
          <p>
            Rearranca el worker de esta misma sesión. El chat conserva su
            historia y el agente <strong>mantiene el contexto</strong>, igual
            que <code>claude --resume</code>.
          </p>
          <button
            type="button"
            className="primary"
            onClick={() => void run("resume")}
            disabled={busy !== null}
          >
            {busy === "resume" ? "Reiniciando…" : "↻ Reiniciar y continuar"}
          </button>
        </div>
        <div className="restart-option">
          <strong>Empezar de cero</strong>
          <p>
            Para esta sesión y abre una nueva del mismo proyecto: chat vacío y
            el agente <strong>sin contexto</strong> previo. Esta conversación
            queda archivada y consultable.
          </p>
          <button
            type="button"
            onClick={() => void run("new")}
            disabled={busy !== null}
          >
            {busy === "new" ? "Creando…" : "✦ Sesión nueva"}
          </button>
        </div>
      </div>
      {err && <div className="modal-error">{err}</div>}
      <div className="modal-actions">
        <button type="button" onClick={onClose} disabled={busy !== null}>
          Cancelar
        </button>
      </div>
    </Modal>
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
        body: { model_profile_id: modelId },
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
