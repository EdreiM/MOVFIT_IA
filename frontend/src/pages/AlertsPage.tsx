import { useEffect, useState } from "react";
import { api } from "../api";

type SystemEvent = {
  id: string;
  kind: "alert" | "report";
  severity: "info" | "warning" | "critical";
  key: string;
  title: string;
  body: string;
  created_at: string | null;
  resolved_at: string | null;
  dismissed_at: string | null;
};

const severityStyle: Record<string, string> = {
  critical: "border-ember/50 text-ember",
  warning: "border-yellow-400/40 text-yellow-300",
  info: "border-white/20 text-sand/60",
};

const severityLabel: Record<string, string> = {
  critical: "crítico",
  warning: "atenção",
  info: "info",
};

function formatDate(value: string | null) {
  return value ? new Date(value).toLocaleString("pt-BR") : "-";
}

export default function AlertsPage() {
  const [alerts, setAlerts] = useState<SystemEvent[]>([]);
  const [reports, setReports] = useState<SystemEvent[]>([]);
  const [error, setError] = useState("");
  const [openReportId, setOpenReportId] = useState<string | null>(null);

  const load = () =>
    Promise.all([
      api<SystemEvent[]>("/admin/events?kind=alert&active=true"),
      api<SystemEvent[]>("/admin/events?kind=report&limit=14"),
    ])
      .then(([a, r]) => {
        setAlerts(a);
        setReports(r);
        setError("");
      })
      .catch((e) => setError(e.message));

  useEffect(() => {
    load();
    const timer = setInterval(load, 60_000);
    return () => clearInterval(timer);
  }, []);

  const dismiss = async (id: string) => {
    try {
      await api(`/admin/events/${id}/dismiss`, { method: "POST" });
      await load();
    } catch (e) {
      setError((e as Error).message);
    }
  };

  return (
    <div className="space-y-10">
      <div>
        <h2 className="font-display text-3xl font-bold">Alertas</h2>
        <p className="mt-1 text-sand/55">
          O sistema confere a cada 5 minutos se ferramentas estão falhando, se transferências não
          chegaram a ninguém e se há clientes sem resposta. O alerta some sozinho quando o problema
          passa.
        </p>
      </div>

      {error && <p className="text-ember">{error}</p>}

      <section className="space-y-3">
        <h3 className="font-display text-xl font-semibold">Em aberto</h3>
        {alerts.length === 0 ? (
          <p className="border border-white/10 px-4 py-6 text-center text-sand/50">
            Nenhum alerta no momento. Tudo funcionando. ✅
          </p>
        ) : (
          <ul className="space-y-3">
            {alerts.map((a) => (
              <li key={a.id} className="border border-white/10 px-4 py-3">
                <div className="flex flex-wrap items-center justify-between gap-2">
                  <div className="flex items-center gap-3">
                    <span
                      className={`rounded-md border px-2 py-0.5 text-xs uppercase ${severityStyle[a.severity]}`}
                    >
                      {severityLabel[a.severity]}
                    </span>
                    <span className="font-medium">{a.title}</span>
                  </div>
                  <button
                    type="button"
                    onClick={() => dismiss(a.id)}
                    className="rounded-md border border-white/15 px-3 py-1 text-xs text-sand/70 hover:bg-white/5"
                  >
                    Dispensar
                  </button>
                </div>
                <p className="mt-2 text-sm text-sand/70">{a.body}</p>
                <p className="mt-1 text-xs text-sand/40">Desde {formatDate(a.created_at)}</p>
              </li>
            ))}
          </ul>
        )}
      </section>

      <section className="space-y-3">
        <h3 className="font-display text-xl font-semibold">Relatórios diários</h3>
        <p className="text-sm text-sand/50">
          Gerado todo dia de manhã com o que aconteceu no dia anterior.
        </p>
        {reports.length === 0 ? (
          <p className="border border-white/10 px-4 py-6 text-center text-sand/50">
            O primeiro relatório aparece amanhã de manhã.
          </p>
        ) : (
          <ul className="divide-y divide-white/10 border border-white/10">
            {reports.map((r) => (
              <li key={r.id} className="px-4 py-3">
                <button
                  type="button"
                  onClick={() => setOpenReportId(openReportId === r.id ? null : r.id)}
                  className="flex w-full items-center justify-between text-left"
                >
                  <span className="font-medium">{r.title}</span>
                  <span className="text-xs text-sand/40">
                    {openReportId === r.id ? "fechar ▲" : "ver ▼"}
                  </span>
                </button>
                {openReportId === r.id && (
                  <pre className="mt-3 whitespace-pre-wrap rounded-md border border-white/10 bg-ink px-3 py-2 text-sm text-sand/80">
                    {r.body}
                  </pre>
                )}
              </li>
            ))}
          </ul>
        )}
      </section>
    </div>
  );
}
