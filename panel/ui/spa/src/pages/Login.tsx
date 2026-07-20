// Login page — usa /api/v1/login/ (FASE C.3) que valida credenciales + TOTP
// y devuelve JSON { ok, user, next }. La sesión Django vive en la cookie
// csrftoken + sessionid (que ya gestiona el backend).

import { useState } from "react";
import { api, ApiError } from "@/lib/api";
import type { CurrentUser } from "@/lib/me";

export function LoginPage() {
  const [username, setUsername] = useState("");
  const [password, setPassword] = useState("");
  const [otp, setOtp] = useState("");
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  async function submit(e: React.FormEvent) {
    e.preventDefault();
    setError(null);
    setBusy(true);
    try {
      const res = await api<{ ok: boolean; user?: CurrentUser; next?: string }>(
        "/api/v1/login/",
        { method: "POST", body: { username, password, otp_token: otp } },
      );
      if (res.ok && res.user?.is_verified) {
        window.location.href = res.next || "/sessions";
      } else if (res.ok) {
        setError("Login ok pero falta código TOTP");
      } else {
        setError("Login falló");
      }
    } catch (err: unknown) {
      if (err instanceof ApiError) {
        if (err.status === 401) setError("Usuario o contraseña incorrectos");
        else if (err.status === 403) setError("Falta CSRF — recarga la página");
        else if (err.status === 400) {
          const body = err.body as { error?: string } | null;
          setError(body?.error || `Error ${err.status}`);
        } else setError(`HTTP ${err.status}`);
      } else {
        setError(String(err));
      }
    } finally {
      setBusy(false);
    }
  }

  return (
    <form className="login" onSubmit={submit}>
      <h1>Login</h1>
      {error && <div className="msg error">{error}</div>}
      <input
        placeholder="usuario"
        value={username}
        onChange={(e) => setUsername(e.target.value)}
        autoComplete="username"
        required
      />
      <input
        type="password"
        placeholder="contraseña"
        value={password}
        onChange={(e) => setPassword(e.target.value)}
        autoComplete="current-password"
        required
      />
      <input
        placeholder="código TOTP (6 dígitos)"
        value={otp}
        onChange={(e) => setOtp(e.target.value)}
        inputMode="numeric"
        pattern="[0-9]{6}"
        autoComplete="one-time-code"
        required
      />
      <button type="submit" className="primary" disabled={busy}>
        {busy ? "Entrando…" : "Entrar"}
      </button>
    </form>
  );
}