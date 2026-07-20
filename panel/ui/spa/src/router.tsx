// Router manual (no file-based) — control total sobre las rutas.
// Las definimos con createRoute() en lugar de createFileRoute() para
// evitar depender del plugin de generación de tipos.

import { createRouter, createRoute, createRootRoute, Outlet, redirect } from "@tanstack/react-router";
import { QueryClient } from "@tanstack/react-query";
import { LoginPage } from "@/pages/Login";
import { SessionsPage } from "@/pages/Sessions";
import { SessionPage } from "@/pages/SessionDetail";
import { ProjectsPage } from "@/pages/Projects";
import { McpsPage } from "@/pages/Mcps";
import { GithubPage } from "@/pages/Github";
import { PermissionsPage } from "@/pages/Permissions";
import { fetchMe, type CurrentUser } from "@/lib/me";

export interface RouterContext {
  queryClient: QueryClient;
  me: CurrentUser | null;
}

const rootRoute = createRootRoute({
  component: RootLayout,
});

function RootLayout() {
  // Si NO hay sesión, mostramos un header mínimo (solo el título).
  // Si la hay, mostramos los enlaces a las secciones.
  // `me` viene del contexto del router (actualizado en boot()).
  const me = (router.options.context as { me?: import("@/lib/me").CurrentUser | null } | undefined)?.me ?? null;
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
  beforeLoad: async () => {
    // Si hay sesión, redirige a /sessions; si no, muestra Login.
    try {
      const me = await fetchMe();
      if (me?.is_verified) throw redirect({ to: "/sessions" });
    } catch (e) {
      if ((e as { isRedirect?: boolean })?.isRedirect) throw e;
      // 401 o red caída: muestra login.
    }
  },
  component: LoginPage,
});

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

const permissionsRoute = createRoute({
  getParentRoute: () => rootRoute,
  path: "/permissions",
  component: PermissionsPage,
});

const routeTree = rootRoute.addChildren([
  indexRoute,
  sessionsRoute,
  sessionDetailRoute,
  projectsRoute,
  mcpsRoute,
  githubRoute,
  permissionsRoute,
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
