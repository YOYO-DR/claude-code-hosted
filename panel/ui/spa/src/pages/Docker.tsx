// SP15 — vista de contenedores Docker.
// Los contenedores que el agente levanta en un proyecto sobreviven a parar la
// sesión; aquí se ven y se paran. Los grupos de docker compose se paran juntos.
// Solo `stop` (SIGTERM + timeout): NO borra contenedores ni volúmenes, así que
// lo parado se puede volver a arrancar con sus datos intactos.
// Los contenedores de la propia plataforma no aparecen (los filtra el backend).

import { useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { api } from "@/lib/api";
import { Modal } from "@/components/Modal";

interface Container {
  id: string;
  full_id: string;
  name: string;
  state: string;
  running: boolean;
  status: string;
  image: string;
  project: string;
  service: string;
  ports: string;
}
interface Group {
  project: string;
  containers: Container[];
  total: number;
  running: number;
}
interface DockerData {
  groups: Group[];
  standalone: Container[];
  unavailable?: string;
}

function StateDot({ running }: { running: boolean }) {
  return (
    <span
      className={`dk-dot${running ? " on" : ""}`}
      title={running ? "corriendo" : "parado"}
      aria-hidden
    />
  );
}

function ContainerRow({
  c,
  busy,
  onStop,
}: {
  c: Container;
  busy: boolean;
  onStop: () => void;
}) {
  return (
    <li className="dk-row">
      <StateDot running={c.running} />
      <span className="dk-row-main">
        <code className="dk-name">{c.service || c.name}</code>
        <span className="meta dk-image" title={c.image}>{c.image}</span>
        {c.ports && <span className="meta dk-ports" title={c.ports}>{c.ports}</span>}
      </span>
      <span className="meta dk-status">{c.status}</span>
      {c.running ? (
        <button disabled={busy} onClick={onStop} title={`Detener ${c.name}`}>
          Detener
        </button>
      ) : (
        <span className="tag dk-tag-off">parado</span>
      )}
    </li>
  );
}

export function DockerPage() {
  const qc = useQueryClient();
  const [confirm, setConfirm] = useState<
    { kind: "project"; name: string; count: number } | { kind: "container"; name: string; ref: string } | null
  >(null);
  const [error, setError] = useState<string | null>(null);

  const q = useQuery({
    queryKey: ["docker"],
    queryFn: () => api<DockerData>("/api/v1/docker/"),
    refetchInterval: 5000,
  });

  const stopMut = useMutation({
    mutationFn: (body: { container?: string; project?: string }) =>
      api("/api/v1/docker/stop/", { method: "POST", body }),
    onSuccess: () => {
      setConfirm(null);
      setError(null);
      void qc.invalidateQueries({ queryKey: ["docker"] });
    },
    onError: (e) => setError(String(e)),
  });

  if (q.isLoading) return <p className="meta">Cargando contenedores…</p>;
  if (q.error) return <p className="msg error">Error: {String(q.error)}</p>;
  const data = q.data;
  if (!data) return null;

  const busy = stopMut.isPending;
  const nothing = data.groups.length === 0 && data.standalone.length === 0;

  return (
    <section>
      <div className="dk-head">
        <h2 style={{ margin: 0 }}>Contenedores Docker</h2>
        <span className="meta">
          Detener manda SIGTERM — no borra contenedores ni volúmenes.
        </span>
      </div>

      {data.unavailable && (
        <p className="msg warn">Docker no disponible: {data.unavailable}</p>
      )}
      {error && <p className="msg error">{error}</p>}
      {nothing && !data.unavailable && (
        <p className="meta">No hay contenedores (aparte de los de la plataforma).</p>
      )}

      {data.groups.map((g) => (
        <div key={g.project} className="card dk-group">
          <div className="dk-group-head">
            <StateDot running={g.running > 0} />
            <strong className="dk-group-name">{g.project}</strong>
            <span className="meta">
              {g.running > 0
                ? `${g.running}/${g.total} corriendo`
                : `${g.total} parado${g.total === 1 ? "" : "s"}`}
            </span>
            {g.running > 0 && (
              <button
                className="danger"
                disabled={busy}
                onClick={() =>
                  setConfirm({ kind: "project", name: g.project, count: g.running })
                }
                title={`Detener los ${g.running} contenedores de ${g.project}`}
              >
                Detener servicio
              </button>
            )}
          </div>
          <ul className="dk-list">
            {g.containers.map((c) => (
              <ContainerRow
                key={c.full_id}
                c={c}
                busy={busy}
                onStop={() =>
                  setConfirm({ kind: "container", name: c.name, ref: c.full_id })
                }
              />
            ))}
          </ul>
        </div>
      ))}

      {data.standalone.length > 0 && (
        <div className="card dk-group">
          <div className="dk-group-head">
            <strong className="dk-group-name">Sueltos</strong>
            <span className="meta">sin docker compose</span>
          </div>
          <ul className="dk-list">
            {data.standalone.map((c) => (
              <ContainerRow
                key={c.full_id}
                c={c}
                busy={busy}
                onStop={() =>
                  setConfirm({ kind: "container", name: c.name, ref: c.full_id })
                }
              />
            ))}
          </ul>
        </div>
      )}

      <Modal
        open={!!confirm}
        title={
          confirm?.kind === "project"
            ? `¿Detener el servicio ${confirm.name}?`
            : `¿Detener ${confirm?.name}?`
        }
        variant="confirm"
        confirmLabel="Detener"
        danger
        busy={busy}
        onCancel={() => setConfirm(null)}
        onConfirm={() => {
          if (!confirm) return;
          if (confirm.kind === "project") stopMut.mutate({ project: confirm.name });
          else stopMut.mutate({ container: confirm.ref });
        }}
      >
        {confirm?.kind === "project" ? (
          <p>
            Se detendrán los <strong>{confirm.count}</strong> contenedores del
            servicio. No se borra nada: los volúmenes y los datos quedan
            intactos y puedes volver a arrancarlo.
          </p>
        ) : (
          <p>
            Se detendrá el contenedor. No se borra nada — se puede volver a
            arrancar con sus datos.
          </p>
        )}
      </Modal>
    </section>
  );
}
