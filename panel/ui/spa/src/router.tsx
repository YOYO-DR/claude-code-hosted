// Router manual (no file-based) — control total sobre las rutas.
// Las definimos con createRoute() en lugar de createFileRoute() para
// evitar depender del plugin de generación de tipos.

import { createContext, useContext } from "react";
import {
  createRouter, createRoute, createRootRoute, Outlet,
} from "@tanstack/react-router";
import { QueryClient } from "@tanstack/react-query";
import { LoginPage } from "@/pages/Login";
import { SessionsPage } from "@/pages/Sessions";
import { SessionPage } from "@/pages/SessionDetail";
import { ProjectsPage } from "@/pages/Projects";
import { McpsPage } from "@/pages/Mcps";
import { GithubPage } from "@/pages/Github";
import { PermissionsPage } from "@/pages/Permissions";
import { ModelsPage } from "@/pages/Models";
import { DockerPage } from "@/pages/Docker";
import type { CurrentUser } from "@/lib/me";

export interface RouterContext {
  queryClient: QueryClient;
}

// ---------- AuthContext: estado React real para que el navbar re-renderice ----------

interface AuthCtx {
  me: CurrentUser | null;
  setMe: (m: CurrentUser | null) => void;
  refresh: () => Promise<void>;
}
export const AuthContext = createContext<AuthCtx>({
  me: null,
  setMe: () => {},
  refresh: async () => {},
});
export function useAuth(): AuthCtx {
  return useContext(AuthContext);
}

const rootRoute = createRootRoute({
  component: RootLayout,
});

function RootLayout() {
  // Sin useEffect(refresh) aquí — AuthProvider ya hace refresh al mount.
  // Si lo añadiéramos, cada re-render del RootLayout dispararía otro fetch
  // → loop /api/v1/me/ cada segundo. El caller que necesite refrescar
  // (login, logout, settings) llama a refresh() explícitamente.
  const { me } = useAuth();
  const authed = !!me?.is_verified;
  return (
    <div style={{ minHeight: "100vh", display: "flex", flexDirection: "column" }}>
      <header className="app-header">
        <strong>
          <a href="/" style={{ textDecoration: "none", color: "inherit" }}>
            Claude Code · Panel
          </a>
        </strong>
        {authed && (
          <nav>
            <a href="/sessions">Sesiones</a>
            <a href="/projects">Proyectos</a>
            <a href="/mcps">MCPs</a>
            <a href="/github">GitHub</a>
            <a href="/models">Modelos</a>
            <a href="/docker">Docker</a>
            <a href="/permissions">Aprobaciones</a>
          </nav>
        )}
        {authed && me && (
          <span className="right">
            <span>{me.username}</span>
            <form
              method="post"
              action="/api/v1/logout/"
              onSubmit={(e) => {
                e.preventDefault();
                void fetch("/api/v1/logout/", {
                  method: "POST", credentials: "include",
                }).then(() => { window.location.href = "/"; });
              }}
            >
              <button type="submit">Salir</button>
            </form>
          </span>
        )}
      </header>
      <main>
        <Outlet />
      </main>
    </div>
  );
}

const indexRoute = createRoute({
  getParentRoute: () => rootRoute,
  path: "/",
  component: IndexRedirect,
});

function IndexRedirect() {
  // Si hay sesión, redirige a /sessions; si no, muestra Login.
  // Usa useAuth (estado React) en vez de fetchMe directo para evitar
  // un round-trip extra.
  const { me } = useAuth();
  if (me?.is_verified) {
    // Renderizamos un redirect client-side (window.location.href).
    window.location.href = "/sessions";
    return null;
  }
  return <LoginPage />;
}

const sessionsRoute = createRoute({
  getParentRoute: () => rootRoute,
  path: "/sessions",
  component: SessionsPage,
});

const sessionDetailRoute = createRoute({
  getParentRoute: () => rootRoute,
  path: "/sessions/$sid",
  component: SessionPage,
});

const projectsRoute = createRoute({
  getParentRoute: () => rootRoute,
  path: "/projects",
  component: ProjectsPage,
});

const mcpsRoute = createRoute({
  getParentRoute: () => rootRoute,
  path: "/mcps",
  component: McpsPage,
});

const githubRoute = createRoute({
  getParentRoute: () => rootRoute,
  path: "/github",
  component: GithubPage,
});

const modelsRoute = createRoute({
  getParentRoute: () => rootRoute,
  path: "/models",
  component: ModelsPage,
});

const permissionsRoute = createRoute({
  getParentRoute: () => rootRoute,
  path: "/permissions",
  component: PermissionsPage,
});

const dockerRoute = createRoute({
  getParentRoute: () => rootRoute,
  path: "/docker",
  component: DockerPage,
});

const routeTree = rootRoute.addChildren([
  indexRoute,
  sessionsRoute,
  sessionDetailRoute,
  projectsRoute,
  mcpsRoute,
  githubRoute,
  modelsRoute,
  permissionsRoute,
  dockerRoute,
]);

export const router = createRouter({
  routeTree,
  context: { queryClient: undefined!, me: null },
});

declare module "@tanstack/react-router" {
  interface Register {
    router: typeof router;
  }
}
