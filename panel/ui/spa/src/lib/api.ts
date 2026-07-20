// Cliente HTTP con CSRF + credentials.
//
// El panel Django usa cookies de sesión + csrf_token. El SPA NUNCA maneja
// JWT — hace fetch con credentials: 'include' y lee/escribe la cookie
// csrf. Para POST/PATCH/DELETE hay que leer el token de la cookie
// `csrftoken` y mandarlo en el header `X-CSRFToken`.

function getCsrfToken(): string {
  const m = document.cookie.match(/(?:^|;\s*)csrftoken=([^;]+)/);
  return m && m[1] ? decodeURIComponent(m[1]) : "";
}

export class ApiError extends Error {
  constructor(public status: number, message: string, public body?: unknown) {
    super(message);
    this.name = "ApiError";
  }
}

export interface ApiOptions extends Omit<RequestInit, "body"> {
  body?: unknown;
  json?: boolean;
}

export async function api<T>(path: string, opts: ApiOptions = {}): Promise<T> {
  const headers: Record<string, string> = {
    Accept: "application/json",
    ...(opts.headers as Record<string, string> | undefined),
  };
  let body: BodyInit | undefined;
  if (opts.body !== undefined) {
    if (opts.json === false) {
      body = opts.body as BodyInit;
    } else {
      headers["Content-Type"] = "application/json";
      body = JSON.stringify(opts.body);
    }
  }
  // CSRF solo para métodos no-seguros.
  const method = (opts.method ?? "GET").toUpperCase();
  if (!["GET", "HEAD", "OPTIONS"].includes(method)) {
    const tok = getCsrfToken();
    if (tok) headers["X-CSRFToken"] = tok;
  }
  const res = await fetch(path, {
    ...opts,
    method,
    headers,
    body,
    credentials: "include",
  });
  if (!res.ok) {
    let body: unknown = undefined;
    try {
      body = await res.json();
    } catch {
      body = await res.text().catch(() => "");
    }
    throw new ApiError(res.status, `HTTP ${res.status} en ${path}`, body);
  }
  if (res.status === 204) return undefined as T;
  return (await res.json()) as T;
}