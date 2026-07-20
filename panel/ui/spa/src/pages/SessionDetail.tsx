// Vista Sesión (FASE C.4) — Layout OpenHands: chat izquierda, panel lateral derecho.
//
// Chat: discriminated union UIEvent v1 por kind (FASE B).
//   - agent_text → burbuja con streaming cursor si streaming=true
//   - agent_thinking → colapsable
//   - tool_call → tarjeta con input; tool_result del mismo tool_use_id se une
//   - permission_request → inline con botones Permitir/Denegar
//   - run_result → tarjeta de resumen
//   - session_status, git_branch, error → tarjetas discretas
// Panel lateral: placeholders (FASE C.5 archivos+diff, C.6 rama).

import { useEffect, useRef, useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { api } from "@/lib/api";
import { openSessionWs, type RawEventMessage } from "@/lib/ws";
import type { UIEvent, UIEventKind } from "@/types/uievents";

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

export function SessionPage() {
  const params = new URLSearchParams(window.location.search);
  const sid = window.location.pathname.split("/").filter(Boolean)[1] ?? params.get("sid") ?? "";
  const sessQ = useSession(sid);
  const [bubbles, setBubbles] = useState<Bubble[]>([]);
  const [wsState, setWsState] = useState<string>("connecting");
  const [input, setInput] = useState("");
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

  if (sessQ.isLoading) return <p>Cargando sesión…</p>;
  if (sessQ.error) return <p>Error: {String(sessQ.error)}</p>;
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
      <p>
        <a href="/sessions">← Sesiones</a>
      </p>
      <h1>
        Sesión <span style={{ opacity: 0.6, fontSize: "0.8em" }}>{sid.slice(0, 8)}</span>
      </h1>
      <p style={{ display: "flex", gap: "1rem", alignItems: "center", flexWrap: "wrap" }}>
        <span>Proyecto: <strong>{sess.project}</strong></span>
        <span>Estado: <span style={{ border: "1px solid #8886", padding: "0 0.4rem", borderRadius: 4 }}>{sess.status}</span></span>
        <span>Costo: ${sess.total_cost_usd.toFixed(4)}</span>
        <span>WS: {wsState}</span>
        <button onClick={() => stopMut.mutate()} disabled={stopMut.isPending}>■ Stop</button>
      </p>
      {sess.github_warn_no_push && (
        <div
          style={{
            background: "#fff3cd",
            color: "#664d03",
            border: "1px solid #ffe69c",
            padding: "0.5rem",
            borderRadius: 4,
            marginBottom: "0.8rem",
          }}
        >
          ⚠️ El PAT actual no tiene permisos de push sobre el repo.
          <code>git push</code> y PR fallarán.
        </div>
      )}

      <div style={{ display: "grid", gridTemplateColumns: "2fr 1fr", gap: "1rem", alignItems: "start" }}>
        <section
          style={{
            border: "1px solid #8884",
            borderRadius: 6,
            background: "#0001",
            height: "60vh",
            display: "flex",
            flexDirection: "column",
          }}
        >
          <div ref={scrollerRef} style={{ flex: 1, overflowY: "auto", padding: "0.5rem" }}>
            {bubbles.length === 0 && <p style={{ opacity: 0.6 }}>Esperando eventos…</p>}
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
            onSubmit={(e) => {
              e.preventDefault();
              const text = input.trim();
              if (!text) return;
              sendMut.mutate(text);
              setInput("");
            }}
            style={{ borderTop: "1px solid #8884", display: "flex", padding: "0.4rem", gap: "0.4rem" }}
          >
            <textarea
              value={input}
              onChange={(e) => setInput(e.target.value)}
              onKeyDown={sendOnEnter}
              placeholder="Mensaje al agente (Ctrl/Cmd+Enter)"
              rows={2}
              style={{ flex: 1, resize: "vertical" }}
            />
            <button type="submit" disabled={sendMut.isPending}>Enviar</button>
          </form>
        </section>

        <aside style={{ display: "grid", gap: "0.5rem" }}>
          <h3>Proyecto</h3>
          <p><a href={`/projects/${sess.project_slug}`}>{sess.project}</a></p>
          <h3>Archivos</h3>
          <p style={{ opacity: 0.6, fontSize: "0.9em" }}>(FASE C.5: tree + file browser con guard de path traversal)</p>
          <h3>Cambios</h3>
          <p style={{ opacity: 0.6, fontSize: "0.9em" }}>(FASE C.5: git diff)</p>
          <h3>Rama</h3>
          <p style={{ opacity: 0.6, fontSize: "0.9em" }}>(FASE C.6: git_branch watcher en vivo)</p>
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
  const cardStyle: React.CSSProperties = {
    margin: "0.3rem 0",
    padding: "0.4rem 0.6rem",
    borderLeft: "3px solid #8886",
    background: "#fff1",
  };
  const p = ui.payload as Record<string, unknown>;
  switch (ui.kind) {
    case "agent_text":
      return (
        <div style={{ ...cardStyle, borderLeftColor: "#4a90d9" }}>
          <span>{String(p.text ?? "")}</span>
          {p.streaming ? <span style={{ opacity: 0.5 }}>▍</span> : null}
          {p.from_user ? <div style={{ opacity: 0.6, fontSize: "0.85em" }}>(usuario)</div> : null}
        </div>
      );
    case "agent_thinking":
      return (
        <details style={{ ...cardStyle, borderLeftColor: "#a8a" }}>
          <summary style={{ opacity: 0.7, cursor: "pointer" }}>pensando…</summary>
          <pre style={{ whiteSpace: "pre-wrap", margin: 0 }}>{String(p.text ?? "")}</pre>
        </details>
      );
    case "tool_call": {
      const awaiting = Boolean(p.awaiting_permission);
      const hasResult = Boolean(bubble.result);
      return (
        <div style={{ ...cardStyle, borderLeftColor: "#e8a" }}>
          <div style={{ display: "flex", gap: "0.5rem", alignItems: "center" }}>
            <code>{String(p.name ?? "")}</code>
            {awaiting && (
              <span style={{ background: "#f0c", color: "#fff", padding: "0 0.4rem", borderRadius: 4 }}>
                esperando permiso
              </span>
            )}
            {hasResult && <span style={{ opacity: 0.6, fontSize: "0.85em" }}>(resultado ↓)</span>}
          </div>
          <pre style={{ whiteSpace: "pre-wrap", margin: "0.3rem 0 0", fontSize: "0.85em", maxHeight: 200, overflow: "auto" }}>
            {JSON.stringify(p.input ?? {}, null, 2)}
          </pre>
          {hasResult && bubble.result?.ui_event && (
            <ToolResultView ui={bubble.result.ui_event} />
          )}
        </div>
      );
    }
    case "permission_request": {
      const id = String(p.id ?? "");
      return (
        <div style={{ ...cardStyle, borderLeftColor: "#c33", background: "#fff3" }}>
          <div>
            <strong>{String(p.tool ?? "")}</strong>
            <span style={{ opacity: 0.6, fontSize: "0.85em" }}> · id={id.slice(0, 8)}</span>
          </div>
          <pre style={{ whiteSpace: "pre-wrap", margin: "0.3rem 0", fontSize: "0.85em" }}>
            {String(p.input_preview ?? "")}
          </pre>
          <div style={{ display: "flex", gap: "0.4rem" }}>
            <button onClick={() => onResolvePerm(id, "allow")} disabled={resolving}>Permitir</button>
            <button onClick={() => onResolvePerm(id, "deny")} disabled={resolving}>Denegar</button>
          </div>
        </div>
      );
    }
    case "run_result":
      return (
        <div style={{ ...cardStyle, borderLeftColor: "#3c9" }}>
          <strong>{p.ok ? "✓ Turno OK" : "✗ Turno con error"}</strong>{" "}
          <span style={{ opacity: 0.7 }}>
            ${Number(p.cost_usd ?? 0).toFixed(4)} · {String(p.num_turns ?? 0)} turnos
          </span>
          {p.summary ? <div style={{ marginTop: "0.3rem" }}>{String(p.summary)}</div> : null}
        </div>
      );
    case "session_status":
      return (
        <div style={{ ...cardStyle, opacity: 0.7, borderLeftColor: "#888" }}>
          session_status: <code>{String(p.status ?? "")}</code>
          {p.model ? <> · model=<code>{String(p.model)}</code></> : null}
        </div>
      );
    case "git_branch":
      return (
        <div style={{ ...cardStyle, borderLeftColor: "#888" }}>
          rama: <code>{String(p.branch ?? "")}</code>
          {p.dirty ? <span style={{ color: "#c33" }}> ● dirty</span> : null}
        </div>
      );
    case "error":
      return (
        <div style={{ ...cardStyle, borderLeftColor: "#c33", background: "#fdd" }}>
          {String(p.message ?? "error")}
        </div>
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
    <div
      style={{
        marginTop: "0.4rem",
        padding: "0.3rem 0.5rem",
        background: ok ? "#dfd" : "#fdd",
        borderRadius: 4,
        fontSize: "0.85em",
      }}
    >
      <strong>{ok ? "OK" : "ERROR"}</strong>
      <pre style={{ whiteSpace: "pre-wrap", margin: "0.2rem 0 0", maxHeight: 200, overflow: "auto" }}>
        {typeof p.output === "string" ? p.output : JSON.stringify(p.output ?? "", null, 2)}
      </pre>
    </div>
  );
}