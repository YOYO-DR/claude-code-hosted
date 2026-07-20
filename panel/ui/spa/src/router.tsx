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
  component: () => (
    <div style={{ minHeight: "100vh", display: "flex", flexDirection: "column" }}>
      <header style={{
        display: "flex", gap: "1rem", alignItems: "center",
        padding: "0.6rem 1rem", borderBottom: "1px solid #8884",
      }}>
        <strong>
          <a href="/" style={{ textDecoration: "none", color: "inherit" }}>Claude Code · Panel</a>
        </strong>
        <nav style={{ display: "flex", gap: "0.8rem" }}>
          <a href="/sessions" style={{ textDecoration: "none" }}>Sesiones</a>
          <a href="/projects" style={{ textDecoration: "none" }}>Proyectos</a>
          <a href="/mcps" style={{ textDecoration: "none" }}>MCPs</a>
          <a href="/github" style={{ textDecoration: "none" }}>GitHub</a>
          <a href="/permissions" style={{ textDecoration: "none" }}>Aprobaciones</a>
        </nav>
      </header>
      <main style={{ flex: 1, padding: "1rem", maxWidth: 1200, margin: "0 auto", width: "100%" }}>
        <Outlet />
      </main>
    </div>
  ),
});

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
