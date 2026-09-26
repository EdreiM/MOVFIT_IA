import { useCallback, useEffect, useRef, useState } from "react";

export type OperationsSummary = {
  conversations_total: number;
  unique_contacts: number;
  messages_inbound: number;
  messages_outbound: number;
  ai_resolved: number;
  transferred: number;
  with_human: number;
  abandoned_by_client: number;
  ai_resolution_rate: number | null;
  plans_presented: number;
  physical_evals_scheduled: number;
  cancellation_requests: number;
  avg_conversations_per_day: number;
};

export type AiOperationsReportData = {
  period_from: string;
  period_to: string;
  generated_at: string;
  ai_name: string;
  summary: OperationsSummary;
  daily_volume: { day: string; conversations: number; inbound_messages: number }[];
  motivations: { label: string; count: number; pct: number | null }[];
  units: { unit: string; conversations: number; plans: number; transfers: number; cancellations: number }[];
  transfer_reasons: { reason: string; count: number }[];
  response_times: {
    median_seconds: number | null;
    median_business_hours_seconds: number | null;
    within_30min_pct: number | null;
    over_4h_pct: number | null;
    samples: number;
  };
};

type Props = {
  fetchReport: (dateFrom: string, dateTo: string) => Promise<AiOperationsReportData>;
  title?: string;
};

function monthStartIso(): string {
  const now = new Date();
  return `${now.getFullYear()}-${String(now.getMonth() + 1).padStart(2, "0")}-01`;
}

function todayIso(): string {
  const now = new Date();
  return `${now.getFullYear()}-${String(now.getMonth() + 1).padStart(2, "0")}-${String(now.getDate()).padStart(2, "0")}`;
}

function formatPeriod(from: string, to: string): string {
  const fmt = (iso: string) => {
    const [y, m, d] = iso.split("-");
    return `${d}/${m}/${y}`;
  };
  return `${fmt(from)} a ${fmt(to)}`;
}

function formatDuration(seconds: number | null): string {
  if (seconds == null) return "—";
  if (seconds < 60) return `${Math.round(seconds)}s`;
  if (seconds < 3600) return `${Math.round(seconds / 60)} min`;
  const h = Math.floor(seconds / 3600);
  const m = Math.round((seconds % 3600) / 60);
  return m > 0 ? `${h}h ${m}min` : `${h}h`;
}

function pct(value: number | null): string {
  if (value == null) return "—";
  return `${Math.round(value * 100)}%`;
}

function num(value: number): string {
  return value.toLocaleString("pt-BR");
}

export default function AiOperationsReport({ fetchReport, title = "Relatório operacional — canal IA" }: Props) {
  const [dateFrom, setDateFrom] = useState(monthStartIso);
  const [dateTo, setDateTo] = useState(todayIso);
  const [report, setReport] = useState<AiOperationsReportData | null>(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState("");
  const printRef = useRef<HTMLDivElement>(null);

  const load = useCallback(async () => {
    setLoading(true);
    setError("");
    try {
      const data = await fetchReport(dateFrom, dateTo);
      setReport(data);
    } catch (e) {
      setError(e instanceof Error ? e.message : "Erro ao carregar relatório");
    } finally {
      setLoading(false);
    }
  }, [dateFrom, dateTo, fetchReport]);

  useEffect(() => {
    load();
  }, [load]);

  const exportPdf = () => {
    document.body.classList.add("printing-operations-report");
    window.print();
    window.setTimeout(() => document.body.classList.remove("printing-operations-report"), 500);
  };

  const s = report?.summary;

  return (
    <>
      <style>{`
        @media print {
          body.printing-operations-report * { visibility: hidden; }
          body.printing-operations-report #operations-report-print,
          body.printing-operations-report #operations-report-print * { visibility: visible; }
          body.printing-operations-report #operations-report-print {
            position: absolute;
            left: 0;
            top: 0;
            width: 100%;
            padding: 24px;
            background: white !important;
            color: #111 !important;
          }
          body.printing-operations-report #operations-report-print .print-hide { display: none !important; }
          body.printing-operations-report #operations-report-print table { border-collapse: collapse; width: 100%; }
          body.printing-operations-report #operations-report-print th,
          body.printing-operations-report #operations-report-print td {
            border: 1px solid #ccc;
            padding: 6px 8px;
            font-size: 11px;
            color: #111 !important;
          }
          body.printing-operations-report #operations-report-print h2,
          body.printing-operations-report #operations-report-print h3,
          body.printing-operations-report #operations-report-print p,
          body.printing-operations-report #operations-report-print span {
            color: #111 !important;
          }
        }
      `}</style>

      <div className="border border-white/10 bg-panel px-5 py-4">
        <div className="flex flex-wrap items-start justify-between gap-4 print-hide">
          <div>
            <p className="text-xs uppercase tracking-wider text-muted">Canal IA / BOTS</p>
            <p className="mt-1 font-display text-xl font-semibold text-sand">{title}</p>
            <p className="mt-1 text-sm text-sand/50">
              Volume, motivos, unidades, transferências e tempo de 1ª resposta — só atendimentos da IA.
            </p>
          </div>
          <div className="flex flex-wrap items-end gap-2">
            <label className="text-xs text-sand/60">
              De
              <input
                type="date"
                value={dateFrom}
                onChange={(e) => setDateFrom(e.target.value)}
                className="mt-1 block rounded border border-white/15 bg-ink px-2 py-1.5 text-sm text-sand"
              />
            </label>
            <label className="text-xs text-sand/60">
              Até
              <input
                type="date"
                value={dateTo}
                onChange={(e) => setDateTo(e.target.value)}
                className="mt-1 block rounded border border-white/15 bg-ink px-2 py-1.5 text-sm text-sand"
              />
            </label>
            <button
              type="button"
              onClick={load}
              disabled={loading}
              className="rounded bg-white/10 px-3 py-2 text-sm text-sand hover:bg-white/15 disabled:opacity-50"
            >
              {loading ? "Atualizando…" : "Atualizar"}
            </button>
            <button
              type="button"
              onClick={exportPdf}
              disabled={!report}
              className="rounded bg-leaf px-3 py-2 text-sm font-semibold text-white hover:bg-lime disabled:opacity-50"
            >
              Exportar PDF
            </button>
          </div>
        </div>

        {error && <p className="mt-4 text-ember print-hide">{error}</p>}
        {loading && !report && <p className="mt-4 text-sand/50 print-hide">Carregando relatório…</p>}

        {report && (
          <div id="operations-report-print" ref={printRef} className="mt-6 space-y-6">
            <div className="border-b border-white/10 pb-4">
              <h2 className="font-display text-2xl font-bold text-sand">
                Relatório de Atendimentos — {report.ai_name}
              </h2>
              <p className="mt-1 text-sm text-sand/70">
                Período: {formatPeriod(report.period_from, report.period_to)} · Canal: IA / BOTS
              </p>
              <p className="text-xs text-sand/45">
                Gerado em{" "}
                {new Date(report.generated_at).toLocaleString("pt-BR", {
                  day: "2-digit",
                  month: "2-digit",
                  year: "numeric",
                  hour: "2-digit",
                  minute: "2-digit",
                })}
              </p>
            </div>

            {s && (
              <section>
                <h3 className="text-sm font-semibold uppercase tracking-wider text-muted">Resumo executivo</h3>
                <div className="mt-3 grid gap-3 sm:grid-cols-2 lg:grid-cols-4">
                  {[
                    { label: "Conversas", value: num(s.conversations_total) },
                    { label: "Contatos únicos", value: num(s.unique_contacts) },
                    { label: "Msgs recebidas", value: num(s.messages_inbound) },
                    { label: "Msgs enviadas", value: num(s.messages_outbound) },
                    { label: "Resolvidos pela IA", value: num(s.ai_resolved) },
                    { label: "Transferidos", value: num(s.transferred) },
                    { label: "Com atendente", value: num(s.with_human) },
                    { label: "Abandonos (cliente parou)", value: num(s.abandoned_by_client) },
                    { label: "Taxa resolução IA", value: pct(s.ai_resolution_rate) },
                    { label: "Planos apresentados", value: num(s.plans_presented) },
                    { label: "Avaliações agendadas", value: num(s.physical_evals_scheduled) },
                    { label: "Pedidos de cancelamento", value: num(s.cancellation_requests) },
                    { label: "Média conversas/dia", value: s.avg_conversations_per_day.toFixed(1) },
                  ].map((item) => (
                    <div key={item.label} className="border-l-2 border-leaf bg-white/5 px-4 py-3">
                      <p className="text-xs uppercase tracking-wider text-muted">{item.label}</p>
                      <p className="mt-1 font-display text-2xl font-bold text-lime">{item.value}</p>
                    </div>
                  ))}
                </div>
              </section>
            )}

            <section>
              <h3 className="text-sm font-semibold uppercase tracking-wider text-muted">Volume diário</h3>
              <div className="mt-3 overflow-x-auto">
                <table className="w-full min-w-[480px] text-left text-sm">
                  <thead>
                    <tr className="text-xs uppercase tracking-wider text-muted">
                      <th className="pb-2 pr-4">Dia</th>
                      <th className="pb-2 pr-4">Conversas</th>
                      <th className="pb-2">Msgs recebidas</th>
                    </tr>
                  </thead>
                  <tbody>
                    {report.daily_volume.map((row) => {
                      const [, m, d] = row.day.split("-");
                      return (
                        <tr key={row.day} className="border-t border-white/10">
                          <td className="py-1.5 pr-4">{d}/{m}</td>
                          <td className="py-1.5 pr-4">{row.conversations}</td>
                          <td className="py-1.5">{row.inbound_messages}</td>
                        </tr>
                      );
                    })}
                  </tbody>
                </table>
              </div>
            </section>

            <section>
              <h3 className="text-sm font-semibold uppercase tracking-wider text-muted">Motivos de atendimento</h3>
              <div className="mt-3 overflow-x-auto">
                <table className="w-full min-w-[420px] text-left text-sm">
                  <thead>
                    <tr className="text-xs uppercase tracking-wider text-muted">
                      <th className="pb-2 pr-4">Motivo</th>
                      <th className="pb-2 pr-4">Qtd</th>
                      <th className="pb-2">%</th>
                    </tr>
                  </thead>
                  <tbody>
                    {report.motivations.length === 0 && (
                      <tr>
                        <td colSpan={3} className="py-3 text-sand/45">Sem conversas no período.</td>
                      </tr>
                    )}
                    {report.motivations.map((m) => (
                      <tr key={m.label} className="border-t border-white/10">
                        <td className="py-1.5 pr-4">{m.label}</td>
                        <td className="py-1.5 pr-4">{m.count}</td>
                        <td className="py-1.5">{pct(m.pct)}</td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            </section>

            <section>
              <h3 className="text-sm font-semibold uppercase tracking-wider text-muted">Por unidade</h3>
              <div className="mt-3 overflow-x-auto">
                <table className="w-full min-w-[560px] text-left text-sm">
                  <thead>
                    <tr className="text-xs uppercase tracking-wider text-muted">
                      <th className="pb-2 pr-4">Unidade</th>
                      <th className="pb-2 pr-4">Conversas</th>
                      <th className="pb-2 pr-4">Planos</th>
                      <th className="pb-2 pr-4">Transferências</th>
                      <th className="pb-2">Cancelamentos</th>
                    </tr>
                  </thead>
                  <tbody>
                    {report.units.map((u) => (
                      <tr key={u.unit} className="border-t border-white/10">
                        <td className="py-1.5 pr-4">{u.unit}</td>
                        <td className="py-1.5 pr-4">{u.conversations}</td>
                        <td className="py-1.5 pr-4">{u.plans}</td>
                        <td className="py-1.5 pr-4">{u.transfers}</td>
                        <td className="py-1.5">{u.cancellations}</td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            </section>

            {report.transfer_reasons.length > 0 && (
              <section>
                <h3 className="text-sm font-semibold uppercase tracking-wider text-muted">Motivos de transferência</h3>
                <div className="mt-3 overflow-x-auto">
                  <table className="w-full min-w-[420px] text-left text-sm">
                    <thead>
                      <tr className="text-xs uppercase tracking-wider text-muted">
                        <th className="pb-2 pr-4">Motivo</th>
                        <th className="pb-2">Qtd</th>
                      </tr>
                    </thead>
                    <tbody>
                      {report.transfer_reasons.map((r) => (
                        <tr key={r.reason} className="border-t border-white/10">
                          <td className="py-1.5 pr-4">{r.reason}</td>
                          <td className="py-1.5">{r.count}</td>
                        </tr>
                      ))}
                    </tbody>
                  </table>
                </div>
              </section>
            )}

            <section>
              <h3 className="text-sm font-semibold uppercase tracking-wider text-muted">Tempo de 1ª resposta da IA</h3>
              <div className="mt-3 grid gap-3 sm:grid-cols-2 lg:grid-cols-4">
                {[
                  { label: "Mediana geral", value: formatDuration(report.response_times.median_seconds) },
                  {
                    label: "Mediana (horário comercial)",
                    value: formatDuration(report.response_times.median_business_hours_seconds),
                  },
                  { label: "Atendidas em até 30 min", value: pct(report.response_times.within_30min_pct) },
                  { label: "Demoraram mais de 4 h", value: pct(report.response_times.over_4h_pct) },
                ].map((item) => (
                  <div key={item.label} className="border border-white/10 px-4 py-3">
                    <p className="text-xs uppercase tracking-wider text-muted">{item.label}</p>
                    <p className="mt-1 text-lg font-semibold text-lime">{item.value}</p>
                  </div>
                ))}
              </div>
              <p className="mt-2 text-xs text-sand/45">
                Amostras: {report.response_times.samples} conversas com resposta da IA registrada.
              </p>
            </section>
          </div>
        )}
      </div>
    </>
  );
}
