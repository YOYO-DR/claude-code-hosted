// Cliente WS de eventos de sesión (FASE B + FASE C).
//
// Contrato del backend (`panel/core/consumers.py`):
//   - Conexión: ws://host/ws/session/<sid>?last_seq=N
//   - Backlog: lee de Postgres (Event) desde seq>N (en orden)
//   - Live: se suscribe al pubsub `out` y emite cada evento nuevo
//   - Cada mensaje: {"seq": N, "type": "...", "payload": {...},
//                    "ui_event": {...} | null, "ts": "..."}
//
// El front consume `ui_event` cuando está, cae a `payload` si es null
// (backfill amigable: eventos previos al despliegue de FASE B).
//
// Reconexión: si la conexión cae, reabre con last_seq = max(seq) visto.
// El consumer reenvía backlog > last_seq desde Postgres (idempotente con
// SeqDedup en el cliente, pero como ordenamos por seq no debería hacer
// falta — mantenemos un Set por seguridad).

import type { UIEvent } from "@/types/uievents";

export interface RawEventMessage {
  seq: number;
  type: string;
  payload: Record<string, unknown>;
  ui_event: UIEvent | null;
  ts: string;
}

export type ConnectionState = "connecting" | "open" | "closed" | "error";

export interface SessionWsClient {
  close(): void;
  state(): ConnectionState;
  lastSeq(): number;
}

export interface SessionWsHandlers {
  onEvent: (ev: RawEventMessage) => void;
  onStateChange?: (state: ConnectionState) => void;
}

export function openSessionWs(
  sid: string,
  handlers: SessionWsHandlers,
  initialSeq = 0,
): SessionWsClient {
  let ws: WebSocket | null = null;
  let lastSeq = initialSeq;
  let state: ConnectionState = "connecting";
  let retries = 0;
  let closed = false;
  const seenSeq = new Set<number>();

  function setState(s: ConnectionState) {
    state = s;
    handlers.onStateChange?.(s);
  }

  function connect() {
    if (closed) return;
    setState("connecting");
    const proto = window.location.protocol === "https:" ? "wss:" : "ws:";
    const url = `${proto}//${window.location.host}/ws/session/${sid}/?last_seq=${lastSeq}`;
    ws = new WebSocket(url);
    ws.onopen = () => {
      retries = 0;
      setState("open");
    };
    ws.onmessage = (ev) => {
      try {
        const data = JSON.parse(ev.data) as RawEventMessage;
        // SeqDedup cliente: por si el backlog y el pubsub se solapan.
        if (seenSeq.has(data.seq)) return;
        seenSeq.add(data.seq);
        if (data.seq > lastSeq) lastSeq = data.seq;
        handlers.onEvent(data);
      } catch (err) {
        console.error("[ws] mensaje no parseable:", err, ev.data);
      }
    };
    ws.onerror = () => {
      setState("error");
    };
    ws.onclose = () => {
      setState("closed");
      if (closed) return;
      // Backoff exponencial cap a 10s.
      const delay = Math.min(1000 * Math.pow(2, retries), 10_000);
      retries++;
      setTimeout(connect, delay);
    };
  }

  connect();

  return {
    close() {
      closed = true;
      ws?.close();
    },
    state: () => state,
    lastSeq: () => lastSeq,
  };
}