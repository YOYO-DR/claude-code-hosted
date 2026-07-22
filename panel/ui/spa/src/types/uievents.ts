// UIEvent v1 — contrato normalizado del backend (FASE B).
// Definido en panel/core/events/normalize.py del backend; este archivo es la
// traducción TypeScript del discriminated union para el front.
//
// Mantener sincronizado con el backend. Si cambias un campo aquí, regenera
// tests/fixtures/normalize_v1_golden.json y actualiza panel/core/events/normalize.py.

export type UIEventKind =
  | "agent_text"
  | "agent_thinking"
  | "tool_call"
  | "tool_result"
  | "permission_request"
  | "permission_resolved"
  | "run_result"
  | "session_status"
  | "git_branch"
  | "context_usage"
  | "user"
  | "error"
  // SP12: cobertura total del SDK.
  | "compact" // system.compact_boundary
  | "rate_limit" // RateLimitEvent
  | "task" // task_started/progress/notification/updated (subagentes)
  | "hook" // hook_started/hook_response (verboso)
  | "slash_commands"; // lista de comandos `/` (efímero, no burbuja)

export interface UIEventBase {
  v: 1;
  seq: number;
  session_id: string;
  ts: string;
  kind: UIEventKind;
  payload: Record<string, unknown>;
}

export interface AgentTextPayload {
  text: string;
  streaming?: boolean;
  from_user?: boolean;
}
export interface UserTextPayload {
  text: string;
  from_user?: boolean;
}
export interface AgentThinkingPayload {
  text?: string;
  tokens?: number;
}
export interface ToolCallPayload {
  tool_use_id: string;
  name: string;
  input: Record<string, unknown>;
  awaiting_permission?: boolean;
  generic?: boolean;
}
export interface ToolResultPayload {
  tool_use_id: string;
  ok: boolean;
  output: unknown;
  truncated: boolean;
}
export interface PermissionRequestPayload {
  id: string;
  tool: string;
  input_preview: string;
  expires_at: string;
}
export interface PermissionResolvedPayload {
  id: string;
  status: "allowed" | "denied" | "expired";
  resolved_by: "web" | "telegram" | "timeout";
}
export interface RunResultPayload {
  ok: boolean;
  cost_usd: number | null;
  num_turns: number;
  summary: string;
  duration_ms?: number;
  stop_reason?: string | null;
}
export interface SessionStatusPayload {
  status: string;
  model?: string | null;
  tools?: string[];
  mcp_servers?: Array<{ name: string }>;
  cwd?: string;
  data?: Record<string, unknown>;
}
export interface GitBranchPayload {
  branch: string;
  dirty: boolean;
}
export interface ContextUsagePayload {
  total_tokens: number;
  max_tokens: number;
  percentage: number;
  model: string;
  auto_compact_threshold?: number | null; // % (1-100)
  auto_compact_enabled?: boolean;
}
export interface ErrorPayload {
  message: string;
  fatal: boolean;
  code?: string;
  source?: string;
}
// SP12
export interface CompactPayload {
  pre_tokens?: number | null;
  trigger?: string | null;
}
export interface RateLimitPayload {
  status?: string | null;
  resets_at?: string | null;
  rate_limit_type?: string | null;
  utilization?: number | null;
  overage_status?: string | null;
}
export interface TaskPayload {
  subtype: string;
  task_id?: string | null;
  description?: string | null;
  status?: string | null;
  summary?: string | null;
  last_tool_name?: string | null;
}
export interface HookPayload {
  subtype: string;
  hook_event_name?: string | null;
  data?: Record<string, unknown>;
}
export interface SlashCommand {
  name: string;
  description: string;
}
export interface SlashCommandsPayload {
  commands: SlashCommand[];
}

// El payload concreto depende del `kind`. Para el front usamos `as` casts
// en cada componente (la discriminated union está implícita).
export type UIEvent = UIEventBase & {
  payload:
    | AgentTextPayload
    | AgentThinkingPayload
    | ToolCallPayload
    | ToolResultPayload
    | PermissionRequestPayload
    | PermissionResolvedPayload
    | RunResultPayload
    | SessionStatusPayload
    | GitBranchPayload
    | ErrorPayload
    | Record<string, unknown>;
};

// Helper para acceso seguro por kind (devuelve {} si el kind no encaja).
export function payloadAs<T>(ev: UIEvent, kind: UIEventKind): T | Record<string, unknown> {
  if (ev.kind !== kind) return {};
  return ev.payload as T;
}