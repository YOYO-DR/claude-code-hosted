// Entry point: monta React + Router + QueryClient.

import React from "react";
import ReactDOM from "react-dom/client";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { RouterProvider } from "@tanstack/react-router";
import { router } from "@/router";
import { fetchMe } from "@/lib/me";
import "@/styles.css";

async function boot() {
  const me = await fetchMe();
  const queryClient = new QueryClient({
    defaultOptions: { queries: { retry: false, staleTime: 5_000 } },
  });
  router.update({ context: { queryClient, me } });

  const rootEl = document.getElementById("root");
  if (!rootEl) throw new Error("#root no existe en index.html");
  ReactDOM.createRoot(rootEl).render(
    <React.StrictMode>
      <QueryClientProvider client={queryClient}>
        <RouterProvider router={router} />
      </QueryClientProvider>
    </React.StrictMode>,
  );
}

boot().catch((err: unknown) => {
  console.error("[boot] fatal:", err);
  const rootEl = document.getElementById("root");
  if (rootEl) {
    rootEl.innerHTML = `<pre style="color:#c33;padding:1rem">boot error: ${String(err)}</pre>`;
  }
});