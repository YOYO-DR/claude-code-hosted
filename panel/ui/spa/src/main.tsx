// Entry point: monta React + Router + QueryClient + AuthProvider.

import React, { useEffect, useState } from "react";
import ReactDOM from "react-dom/client";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { RouterProvider } from "@tanstack/react-router";
import { router, AuthContext } from "@/router";
import { fetchMe, type CurrentUser } from "@/lib/me";
import "@/styles.css";

function AuthProvider({ children }: { children: React.ReactNode }) {
  const [me, setMe] = useState<CurrentUser | null>(null);
  const refresh = async () => {
    const fresh = await fetchMe();
    setMe(fresh);
  };
  useEffect(() => {
    void refresh();
  }, []);
  return (
    <AuthContext.Provider value={{ me, setMe, refresh }}>
      {children}
    </AuthContext.Provider>
  );
}

function boot(): void {
  // NO llamamos fetchMe aquí: AuthProvider ya lo hace en mount.
  // Hacerlo dos veces duplicaba /api/v1/me/ en el boot.
  const queryClient = new QueryClient({
    defaultOptions: { queries: { retry: false, staleTime: 5_000 } },
  });

  const rootEl = document.getElementById("root");
  if (!rootEl) throw new Error("#root no existe en index.html");
  ReactDOM.createRoot(rootEl).render(
    <React.StrictMode>
      <QueryClientProvider client={queryClient}>
        <AuthProvider>
          <RouterProvider
            router={router}
            context={{ queryClient }}
          />
        </AuthProvider>
      </QueryClientProvider>
    </React.StrictMode>,
  );
}

try {
  boot();
} catch (err: unknown) {
  console.error("[boot] fatal:", err);
  const rootEl = document.getElementById("root");
  if (rootEl) {
    rootEl.innerHTML = `<pre style="color:#c33;padding:1rem">boot error: ${String(err)}</pre>`;
  }
}