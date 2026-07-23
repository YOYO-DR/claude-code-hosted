// SP16: tema (light/dark/system) con default `dark`. El default se aplica
// SIN flash de light: el script corre antes de que React monte leyendo
// localStorage y el @media (prefers-color-scheme: light) en :root.
//
// "system" delega en el @media CSS: si el SO está en light, :root recibe las
// vars claras; si está en dark, las dark. El data-theme="light"/"dark" pisa
// el @media para forzar un modo concreto (override del usuario).

export type ThemeMode = "light" | "dark" | "system";

const STORAGE_KEY = "panel-theme";
const DEFAULT: ThemeMode = "dark";   // SP16: oscuro por default

function isValid(s: string | null): s is ThemeMode {
  return s === "light" || s === "dark" || s === "system";
}

export function getTheme(): ThemeMode {
  if (typeof window === "undefined") return DEFAULT;
  const v = window.localStorage.getItem(STORAGE_KEY);
  return isValid(v) ? v : DEFAULT;
}

export function applyTheme(mode: ThemeMode): void {
  if (typeof document === "undefined") return;
  const html = document.documentElement;
  if (mode === "system") {
    html.removeAttribute("data-theme");
  } else {
    html.setAttribute("data-theme", mode);
  }
}

export function setTheme(mode: ThemeMode): void {
  applyTheme(mode);
  try { window.localStorage.setItem(STORAGE_KEY, mode); } catch { /* ignore */ }
}

/** Llamar lo antes posible para evitar el flash. Aplica el modo guardado (o
 *  el default) sincrónicamente. */
export function bootstrapTheme(): void {
  applyTheme(getTheme());
}
