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
  hourly_inbound: { hour: number; inbound_messages: number }[];
  outcomes: { label: string; count: number; pct: number | null }[];
  period_comparison: {
    period_from: string;
    period_to: string;
    rows: { label: string; current: number; previous: number; change_pct: number | null }[];
  } | null;
};

type Props = {
  fetchReport: (dateFrom: string, dateTo: string) => Promise<AiOperationsReportData>;
  title?: string;
};

const CHART_COLORS = ["#7fe37a", "#5fb8ff", "#ff8a5f", "#c084fc", "#fbbf24", "#f87171", "#34d399", "#60a5fa"];

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

function pct(value: number | null | undefined): string {
  if (value == null) return "—";
  return `${Math.round(value * 100)}%`;
}

function num(value: number): string {
  return value.toLocaleString("pt-BR");
}

function changeLabel(change: number | null): string {
  if (change == null) return "—";
  const sign = change > 0 ? "+" : "";
  return `${sign}${Math.round(change * 100)}%`;
}

function monthTitle(iso: string): string {
  const d = new Date(`${iso}T12:00:00`);
  const label = d.toLocaleDateString("pt-BR", { month: "long", year: "numeric" });
  return label.charAt(0).toUpperCase() + label.slice(1);
}

function InsightList({ items }: { items: string[] }) {
  if (items.length === 0) return null;
  return (
    <ul className="mt-3 space-y-1.5 text-sm leading-relaxed text-sand/80">
      {items.map((item, i) => (
        <li key={i} className="flex gap-2">
          <span className="mt-1.5 h-1.5 w-1.5 shrink-0 rounded-full bg-leaf" />
          <span>{item}</span>
        </li>
      ))}
    </ul>
  );
}

function Section({
  n,
  title,
  subtitle,
  children,
}: {
  n: number;
  title: string;
  subtitle?: string;
  children: React.ReactNode;
}) {
  return (
    <section className="report-section break-inside-avoid border-t border-white/10 pt-6">
      <p className="text-xs font-semibold uppercase tracking-widest text-leaf/80">
        {n}. {title}
      </p>
      {subtitle && <p className="mt-1 text-sm text-sand/55">{subtitle}</p>}
      <div className="mt-4">{children}</div>
    </section>
  );
}

function DailyBarChart({ data }: { data: { label: string; value: number }[] }) {
  if (data.length === 0) return <p className="text-sm text-sand/45">Sem dados no período.</p>;
  const max = Math.max(1, ...data.map((d) => d.value));
  const width = 720;
  const height = 180;
  const pad = { t: 8, r: 8, b: 28, l: 8 };
  const innerW = width - pad.l - pad.r;
  const innerH = height - pad.t - pad.b;
  const barW = Math.max(4, innerW / data.length - 2);

  return (
    <svg viewBox={`0 0 ${width} ${height}`} className="w-full" role="img" aria-label="Conversas por dia">
      {data.map((d, i) => {
        const h = (d.value / max) * innerH;
        const x = pad.l + i * (innerW / data.length) + 1;
        const y = pad.t + innerH - h;
        const showLabel = data.length <= 16 || i % Math.ceil(data.length / 12) === 0 || i === data.length - 1;
        return (
          <g key={d.label}>
            <rect x={x} y={y} width={barW} height={Math.max(h, d.value > 0 ? 2 : 0)} fill="#7fe37a" rx={1} />
            {showLabel && (
              <text x={x + barW / 2} y={height - 6} fontSize={9} fill="#9a9a9a" textAnchor="middle">
                {d.label}
              </text>
            )}
          </g>
        );
      })}
    </svg>
  );
}

function HorizontalBarChart({
  items,
  maxItems = 8,
}: {
  items: { label: string; value: number; pct?: number | null }[];
  maxItems?: number;
}) {
  const top = items.slice(0, maxItems);
  if (top.length === 0) return <p className="text-sm text-sand/45">Sem dados.</p>;
  const max = Math.max(1, ...top.map((i) => i.value));

  return (
    <div className="space-y-2.5">
      {top.map((item, idx) => (
        <div key={item.label}>
          <div className="mb-1 flex justify-between gap-2 text-xs">
            <span className="text-sand/80">{item.label}</span>
            <span className="shrink-0 text-sand/55">
              {item.value}
              {item.pct != null ? ` (${pct(item.pct)})` : ""}
            </span>
          </div>
          <div className="h-3 rounded bg-white/5">
            <div
              className="h-3 rounded transition-all"
              style={{
                width: `${Math.max((item.value / max) * 100, item.value > 0 ? 4 : 0)}%`,
                backgroundColor: CHART_COLORS[idx % CHART_COLORS.length],
              }}
            />
          </div>
        </div>
      ))}
    </div>
  );
}

function HourlyBarChart({ data }: { data: { hour: number; inbound_messages: number }[] }) {
  const max = Math.max(1, ...data.map((d) => d.inbound_messages));
  const width = 720;
  const height = 160;
  const pad = { t: 8, r: 8, b: 28, l: 8 };
  const innerW = width - pad.l - pad.r;
  const innerH = height - pad.t - pad.b;
  const barW = innerW / 24 - 1;

  return (
    <svg viewBox={`0 0 ${width} ${height}`} className="w-full" role="img" aria-label="Mensagens por hora">
      {data.map((d) => {
        const h = (d.inbound_messages / max) * innerH;
        const x = pad.l + d.hour * (innerW / 24);
        const y = pad.t + innerH - h;
        const showLabel = d.hour % 3 === 0;
        return (
          <g key={d.hour}>
            <rect
              x={x}
              y={y}
              width={barW}
              height={Math.max(h, d.inbound_messages > 0 ? 2 : 0)}
              fill="#5fb8ff"
              rx={1}
            />
            {showLabel && (
              <text x={x + barW / 2} y={height - 6} fontSize={9} fill="#9a9a9a" textAnchor="middle">
                {String(d.hour).padStart(2, "0")}h
              </text>
            )}
          </g>
        );
      })}
    </svg>
  );
}

function GroupedUnitChart({
  units,
}: {
  units: { unit: string; plans: number; cancellations: number; conversations: number }[];
}) {
  const top = units.filter((u) => u.unit !== "Sem unidade definida").slice(0, 6);
  if (top.length === 0) return null;
  const max = Math.max(1, ...top.flatMap((u) => [u.plans, u.cancellations, u.conversations]));

  return (
    <div className="space-y-3">
      {top.map((u) => (
        <div key={u.unit}>
          <p className="mb-1 text-xs font-medium text-sand/75">{u.unit}</p>
          <div className="grid grid-cols-3 gap-2 text-[10px] uppercase tracking-wide text-sand/45">
            {[
              { label: "Conversas", value: u.conversations, color: "#7fe37a" },
              { label: "Planos", value: u.plans, color: "#5fb8ff" },
              { label: "Cancel.", value: u.cancellations, color: "#f87171" },
            ].map((bar) => (
              <div key={bar.label}>
                <div className="mb-0.5 flex justify-between">
                  <span>{bar.label}</span>
                  <span>{bar.value}</span>
                </div>
                <div className="h-2 rounded bg-white/5">
                  <div
                    className="h-2 rounded"
                    style={{ width: `${Math.max((bar.value / max) * 100, bar.value > 0 ? 6 : 0)}%`, backgroundColor: bar.color }}
                  />
                </div>
              </div>
            ))}
          </div>
        </div>
      ))}
    </div>
  );
}

function buildExecutiveInsights(report: AiOperationsReportData): string[] {
  const s = report.summary;
  const insights: string[] = [];

  if (s.conversations_total === 0) {
    return ["Ainda não há conversas registradas no período selecionado."];
  }

  const topMotivation = report.motivations[0];
  if (topMotivation) {
    insights.push(
      `O motivo mais frequente foi "${topMotivation.label}" (${topMotivation.count} conversas, ${pct(topMotivation.pct)}).`,
    );
  }

  const topUnit = report.units.find((u) => u.unit !== "Sem unidade definida") || report.units[0];
  if (topUnit) {
    insights.push(
      `${topUnit.unit} concentrou ${topUnit.conversations} conversas (${pct(topUnit.conversations / s.conversations_total)} do total).`,
    );
  }

  if (s.abandoned_by_client > 0) {
    insights.push(
      `Alerta: ${s.abandoned_by_client} conversas (${pct(s.abandoned_by_client / s.conversations_total)}) pararam no fluxo da IA sem resolução nem transferência — o cliente foi o último a escrever.`,
    );
  }

  if (s.ai_resolution_rate != null) {
    insights.push(
      `Taxa de resolução pela IA: ${pct(s.ai_resolution_rate)} (resolvidos ÷ resolvidos + transferidos + com atendente).`,
    );
  }

  const undefinedUnit = report.units.find((u) => u.unit === "Sem unidade definida");
  if (undefinedUnit && undefinedUnit.conversations / s.conversations_total > 0.2) {
    insights.push(
      `${pct(undefinedUnit.conversations / s.conversations_total)} das conversas estão sem unidade identificada — vale reforçar captura de CPF/unidade no fluxo.`,
    );
  }

  return insights.slice(0, 5);
}

function buildVolumeInsights(report: AiOperationsReportData): string[] {
  const s = report.summary;
  const days = report.daily_volume.length;
  const [y, m] = report.period_to.split("-").map(Number);
  const daysInMonth = new Date(y, m, 0).getDate();
  const projection = Math.round(s.avg_conversations_per_day * daysInMonth);
  const insights = [
    `Média de ${s.avg_conversations_per_day.toFixed(1)} conversas por dia no período de ${days} dias.`,
  ];
  if (days < daysInMonth) {
    insights.push(
      `Projeção para o mês fechado: cerca de ${num(projection)} conversas, mantendo o ritmo atual.`,
    );
  }
  const peak = [...report.daily_volume].sort((a, b) => b.conversations - a.conversations)[0];
  if (peak && peak.conversations > 0) {
    const [, mo, da] = peak.day.split("-");
    insights.push(`Dia de maior volume: ${da}/${mo} com ${peak.conversations} conversas.`);
  }
  return insights;
}

function buildCancellationInsights(report: AiOperationsReportData): string[] {
  const s = report.summary;
  if (s.cancellation_requests === 0) return ["Nenhum pedido de cancelamento registrado via transferência no período."];
  const worst = [...report.units]
    .filter((u) => u.cancellations > 0)
    .sort((a, b) => b.cancellations - a.cancellations)[0];
  const insights = [
    `${s.cancellation_requests} conversas tiveram transferência por cancelamento.`,
  ];
  if (worst) {
    insights.push(
      `${worst.unit} concentra ${worst.cancellations} pedidos — priorizar roteiro de retenção nessa unidade.`,
    );
  }
  const plansVsCancel = s.plans_presented > 0 ? s.cancellation_requests / s.plans_presented : null;
  if (plansVsCancel != null) {
    insights.push(
      `Relação cancelamentos ÷ planos apresentados: ${(plansVsCancel * 100).toFixed(0)}% (${s.cancellation_requests} cancel. para ${s.plans_presented} planos).`,
    );
  }
  return insights;
}

function buildResponseInsights(report: AiOperationsReportData): string[] {
  const rt = report.response_times;
  const s = report.summary;
  const insights: string[] = [];
  if (rt.median_business_hours_seconds != null) {
    insights.push(
      `No horário comercial (8h–18h, dias úteis), a mediana de 1ª resposta da IA foi ${formatDuration(rt.median_business_hours_seconds)}.`,
    );
  }
  if (rt.within_30min_pct != null) {
    insights.push(`${pct(rt.within_30min_pct)} das conversas receberam a 1ª resposta da IA em até 30 minutos.`);
  }
  if (rt.over_4h_pct != null && rt.over_4h_pct > 0.1) {
    insights.push(
      `Atenção: ${pct(rt.over_4h_pct)} das conversas esperaram mais de 4 horas pela 1ª resposta da IA.`,
    );
  }
  const peakHour = [...report.hourly_inbound].sort((a, b) => b.inbound_messages - a.inbound_messages)[0];
  if (peakHour && peakHour.inbound_messages > 0) {
    insights.push(
      `Pico de mensagens dos clientes: ${String(peakHour.hour).padStart(2, "0")}h–${String(peakHour.hour + 1).padStart(2, "0")}h (${peakHour.inbound_messages} mensagens).`,
    );
  }
  if (s.abandoned_by_client > 0) {
    insights.push(
      `${s.abandoned_by_client} conversas ficaram paradas no fluxo automático (BOTS) sem chegar a uma atendente.`,
    );
  }
  return insights;
}

function buildActionItems(report: AiOperationsReportData): { action: string; priority: string }[] {
  const s = report.summary;
  const items: { action: string; priority: string }[] = [];

  if (s.abandoned_by_client >= 5) {
    items.push({
      action: `Revisar fluxo da IA: ${s.abandoned_by_client} conversas de clientes pararam sem resolução nem transferência.`,
      priority: "Imediato",
    });
  }
  const undefinedUnit = report.units.find((u) => u.unit === "Sem unidade definida");
  if (undefinedUnit && undefinedUnit.conversations >= 10) {
    items.push({
      action: `Reforçar identificação de unidade/CPF — ${undefinedUnit.conversations} conversas sem unidade definida.`,
      priority: "Curto prazo",
    });
  }
  if (s.cancellation_requests >= 3) {
    const worst = [...report.units].sort((a, b) => b.cancellations - a.cancellations)[0];
    items.push({
      action: worst
        ? `Trabalhar retenção em ${worst.unit} (${worst.cancellations} pedidos de cancelamento).`
        : "Trabalhar roteiro de retenção para pedidos de cancelamento.",
      priority: "Curto prazo",
    });
  }
  if (report.response_times.over_4h_pct != null && report.response_times.over_4h_pct > 0.15) {
    items.push({
      action: "Investigar demora na 1ª resposta da IA (mais de 15% acima de 4 horas).",
      priority: "Imediato",
    });
  }
  const topMotivation = report.motivations.find((m) => m.label.includes("planos"));
  if (topMotivation && topMotivation.count >= 10 && s.transferred > s.ai_resolved) {
    items.push({
      action: "Muitas dúvidas sobre planos terminam em transferência — revisar se a IA está fechando o ciclo antes de encaminhar.",
      priority: "Médio prazo",
    });
  }
  if (items.length === 0) {
    items.push({
      action: "Manter monitoramento semanal do relatório operacional da IA.",
      priority: "Contínuo",
    });
  }
  return items.slice(0, 6);
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
  const dailyChart = report
    ? report.daily_volume.map((row) => {
        const [, m, d] = row.day.split("-");
        return { label: `${d}/${m}`, value: row.conversations };
      })
    : [];

  return (
    <>
      <style>{`
        @media print {
          body.printing-operations-report * { visibility: hidden; }
          body.printing-operations-report #operations-report-print,
          body.printing-operations-report #operations-report-print * { visibility: visible; }
          body.printing-operations-report #operations-report-print {
            position: absolute; left: 0; top: 0; width: 100%;
            padding: 20px 24px; background: white !important; color: #111 !important;
          }
          body.printing-operations-report #operations-report-print .print-hide { display: none !important; }
          body.printing-operations-report #operations-report-print .report-section {
            break-inside: avoid; page-break-inside: avoid;
            border-color: #ddd !important; padding-top: 18px; margin-top: 12px;
          }
          body.printing-operations-report #operations-report-print table {
            border-collapse: collapse; width: 100%;
          }
          body.printing-operations-report #operations-report-print th,
          body.printing-operations-report #operations-report-print td {
            border: 1px solid #ccc; padding: 5px 7px; font-size: 10px; color: #111 !important;
          }
          body.printing-operations-report #operations-report-print h2,
          body.printing-operations-report #operations-report-print h3,
          body.printing-operations-report #operations-report-print p,
          body.printing-operations-report #operations-report-print span,
          body.printing-operations-report #operations-report-print li {
            color: #111 !important;
          }
          body.printing-operations-report #operations-report-print .text-lime,
          body.printing-operations-report #operations-report-print .text-leaf { color: #2d6a2e !important; }
          body.printing-operations-report #operations-report-print .bg-white\\/5,
          body.printing-operations-report #operations-report-print .bg-panel { background: #f7f7f7 !important; }
        }
      `}</style>

      <div className="border border-white/10 bg-panel px-5 py-4">
        <div className="flex flex-wrap items-start justify-between gap-4 print-hide">
          <div>
            <p className="text-xs uppercase tracking-wider text-muted">Canal IA / BOTS</p>
            <p className="mt-1 font-display text-xl font-semibold text-sand">{title}</p>
            <p className="mt-1 text-sm text-sand/50">
              Relatório executivo para supervisão — volume, motivos, unidades, gráficos e comentários interpretativos.
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

        {report && s && (
          <div id="operations-report-print" ref={printRef} className="mt-6 space-y-2">
            {/* Capa / cabeçalho */}
            <div className="border-b border-white/10 pb-5">
              <p className="font-display text-sm font-bold uppercase tracking-widest text-leaf">MOV FIT</p>
              <h2 className="mt-1 font-display text-2xl font-bold text-sand">Relatório de Atendimentos — Canal IA</h2>
              <p className="mt-1 text-sm text-sand/70">
                {monthTitle(report.period_to)} — parcial de {formatPeriod(report.period_from, report.period_to)}
              </p>
              <p className="text-sm text-sand/60">
                Assistente: {report.ai_name} · Canal BOTS (IA recebe todas as mensagens de primeira)
              </p>
              <p className="mt-2 text-xs text-sand/45">
                Base: Mônica AI — conversas de {formatPeriod(report.period_from, report.period_to)} · Emitido em{" "}
                {new Date(report.generated_at).toLocaleString("pt-BR")}
              </p>
            </div>

            {/* Resumo executivo */}
            <section className="report-section pt-5">
              <p className="text-xs font-semibold uppercase tracking-widest text-muted">Resumo executivo</p>
              <div className="mt-4 grid gap-3 sm:grid-cols-2 lg:grid-cols-5">
                {[
                  { label: "Conversas", value: num(s.conversations_total) },
                  { label: "Contatos únicos", value: num(s.unique_contacts) },
                  { label: "Planos apresentados", value: num(s.plans_presented) },
                  { label: "Pedidos de cancelamento", value: num(s.cancellation_requests) },
                  { label: "Conversas / dia", value: s.avg_conversations_per_day.toFixed(0) },
                  { label: "Resolução pela IA", value: pct(s.ai_resolution_rate) },
                  { label: "1ª resposta (comercial)", value: formatDuration(report.response_times.median_business_hours_seconds) },
                  { label: "Abandonos no BOTS", value: num(s.abandoned_by_client) },
                  { label: "Sem resposta / transferência", value: pct(s.abandoned_by_client / Math.max(s.conversations_total, 1)) },
                  { label: "Avaliações agendadas", value: num(s.physical_evals_scheduled) },
                ].map((item) => (
                  <div key={item.label} className="border-l-2 border-leaf bg-white/5 px-3 py-2.5">
                    <p className="text-[10px] uppercase tracking-wider text-muted">{item.label}</p>
                    <p className="mt-0.5 font-display text-xl font-bold text-lime">{item.value}</p>
                  </div>
                ))}
              </div>
              <InsightList items={buildExecutiveInsights(report)} />
            </section>

            {/* Comparativo */}
            {report.period_comparison && report.period_comparison.rows.length > 0 && (
              <section className="report-section">
                <p className="text-xs font-semibold uppercase tracking-widest text-muted">
                  Comparativo com período anterior ({formatPeriod(report.period_comparison.period_from, report.period_comparison.period_to)})
                </p>
                <div className="mt-3 overflow-x-auto">
                  <table className="w-full min-w-[480px] text-left text-sm">
                    <thead>
                      <tr className="text-xs uppercase tracking-wider text-muted">
                        <th className="pb-2 pr-4">Indicador</th>
                        <th className="pb-2 pr-4">Período atual</th>
                        <th className="pb-2 pr-4">Período anterior</th>
                        <th className="pb-2">Variação</th>
                      </tr>
                    </thead>
                    <tbody>
                      {report.period_comparison.rows.map((row) => (
                        <tr key={row.label} className="border-t border-white/10">
                          <td className="py-1.5 pr-4">{row.label}</td>
                          <td className="py-1.5 pr-4">
                            {row.label.includes("min") ? row.current.toFixed(0) : num(Math.round(row.current))}
                          </td>
                          <td className="py-1.5 pr-4">
                            {row.label.includes("min") ? row.previous.toFixed(0) : num(Math.round(row.previous))}
                          </td>
                          <td className="py-1.5">{changeLabel(row.change_pct)}</td>
                        </tr>
                      ))}
                    </tbody>
                  </table>
                </div>
              </section>
            )}

            <Section n={1} title="Volume de atendimentos" subtitle="Conversas por dia — canal IA / BOTS">
              <DailyBarChart data={dailyChart} />
              <div className="mt-4 overflow-x-auto">
                <table className="w-full min-w-[400px] text-left text-sm">
                  <tbody>
                    {[
                      ["Conversas totais", num(s.conversations_total), "100%"],
                      ["Média por dia", s.avg_conversations_per_day.toFixed(1), "—"],
                      ["Mensagens recebidas", num(s.messages_inbound), "—"],
                      ["Mensagens enviadas", num(s.messages_outbound), "—"],
                    ].map(([label, value, share]) => (
                      <tr key={label as string} className="border-t border-white/10">
                        <td className="py-1.5 pr-4">{label}</td>
                        <td className="py-1.5 pr-4 font-medium text-lime">{value}</td>
                        <td className="py-1.5">{share}</td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
              <InsightList items={buildVolumeInsights(report)} />
            </Section>

            <Section
              n={2}
              title="Tipos de atendimento"
              subtitle={`Motivos classificados (${num(report.motivations.reduce((a, m) => a + m.count, 0))} conversas)`}
            >
              <HorizontalBarChart items={report.motivations.map((m) => ({ label: m.label, value: m.count, pct: m.pct }))} />
              <div className="mt-4 overflow-x-auto">
                <table className="w-full min-w-[420px] text-left text-sm">
                  <thead>
                    <tr className="text-xs uppercase tracking-wider text-muted">
                      <th className="pb-2 pr-4">Motivo</th>
                      <th className="pb-2 pr-4">Qtd.</th>
                      <th className="pb-2">%</th>
                    </tr>
                  </thead>
                  <tbody>
                    {report.motivations.slice(0, 10).map((m) => (
                      <tr key={m.label} className="border-t border-white/10">
                        <td className="py-1.5 pr-4">{m.label}</td>
                        <td className="py-1.5 pr-4">{m.count}</td>
                        <td className="py-1.5">{pct(m.pct)}</td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            </Section>

            <Section n={3} title="Resultados da IA" subtitle="Planos, avaliações e resoluções">
              <div className="grid gap-3 sm:grid-cols-4">
                {[
                  { label: "Planos apresentados", value: s.plans_presented },
                  { label: "Avaliações agendadas", value: s.physical_evals_scheduled },
                  { label: "Resolvidos pela IA", value: s.ai_resolved },
                  { label: "Transferidos", value: s.transferred },
                ].map((item) => (
                  <div key={item.label} className="border border-white/10 px-4 py-3 text-center">
                    <p className="font-display text-3xl font-bold text-lime">{num(item.value)}</p>
                    <p className="mt-1 text-xs uppercase tracking-wider text-muted">{item.label}</p>
                  </div>
                ))}
              </div>
              {s.plans_presented > 0 && (
                <p className="mt-3 text-sm text-sand/70">
                  Conversão estimada (planos → resolução IA):{" "}
                  {pct(s.ai_resolved / s.plans_presented)} das conversas com planos apresentados foram resolvidas pela IA.
                </p>
              )}
            </Section>

            <Section n={4} title="Cancelamentos" subtitle="Planos apresentados x pedidos de cancelamento por unidade">
              <GroupedUnitChart units={report.units} />
              <div className="mt-4 overflow-x-auto">
                <table className="w-full min-w-[480px] text-left text-sm">
                  <thead>
                    <tr className="text-xs uppercase tracking-wider text-muted">
                      <th className="pb-2 pr-4">Unidade</th>
                      <th className="pb-2 pr-4">Cancelamentos</th>
                      <th className="pb-2 pr-4">Planos</th>
                      <th className="pb-2">Conversas</th>
                    </tr>
                  </thead>
                  <tbody>
                    {[...report.units]
                      .sort((a, b) => b.cancellations - a.cancellations)
                      .slice(0, 8)
                      .map((u) => (
                        <tr key={u.unit} className="border-t border-white/10">
                          <td className="py-1.5 pr-4">{u.unit}</td>
                          <td className="py-1.5 pr-4">{u.cancellations}</td>
                          <td className="py-1.5 pr-4">{u.plans}</td>
                          <td className="py-1.5">{u.conversations}</td>
                        </tr>
                      ))}
                  </tbody>
                </table>
              </div>
              <InsightList items={buildCancellationInsights(report)} />
            </Section>

            <Section n={5} title="Distribuição por unidade" subtitle="Conversas por unidade (cadastro do cliente)">
              <HorizontalBarChart
                items={report.units.map((u) => ({
                  label: u.unit,
                  value: u.conversations,
                  pct: u.conversations / Math.max(s.conversations_total, 1),
                }))}
                maxItems={10}
              />
              <div className="mt-4 overflow-x-auto">
                <table className="w-full min-w-[560px] text-left text-sm">
                  <thead>
                    <tr className="text-xs uppercase tracking-wider text-muted">
                      <th className="pb-2 pr-4">Unidade</th>
                      <th className="pb-2 pr-4">Conversas</th>
                      <th className="pb-2 pr-4">Planos</th>
                      <th className="pb-2 pr-4">Transferências</th>
                      <th className="pb-2">Cancel.</th>
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
            </Section>

            <Section n={6} title="Desempenho do canal IA" subtitle="Para onde foi cada conversa">
              <HorizontalBarChart
                items={report.outcomes.map((o) => ({ label: o.label, value: o.count, pct: o.pct }))}
              />
              <div className="mt-4 overflow-x-auto">
                <table className="w-full min-w-[420px] text-left text-sm">
                  <thead>
                    <tr className="text-xs uppercase tracking-wider text-muted">
                      <th className="pb-2 pr-4">Desfecho</th>
                      <th className="pb-2 pr-4">Qtd.</th>
                      <th className="pb-2">%</th>
                    </tr>
                  </thead>
                  <tbody>
                    {report.outcomes.map((o) => (
                      <tr key={o.label} className="border-t border-white/10">
                        <td className="py-1.5 pr-4">{o.label}</td>
                        <td className="py-1.5 pr-4">{o.count}</td>
                        <td className="py-1.5">{pct(o.pct)}</td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
              <p className="mt-3 text-sm text-sand/70">
                As {num(s.abandoned_by_client)} conversas abandonadas são clientes que escreveram por último e a conversa
                ficou aberta na IA, sem transferência — equivalente ao alerta de “pararam no BOTS” do relatório operacional.
              </p>
            </Section>

            <Section n={7} title="Tempo de resposta" subtitle="1ª resposta da IA e distribuição por hora">
              <div className="grid gap-3 sm:grid-cols-2 lg:grid-cols-4">
                {[
                  { label: "Mediana geral", value: formatDuration(report.response_times.median_seconds) },
                  { label: "Mediana (8h–18h, dias úteis)", value: formatDuration(report.response_times.median_business_hours_seconds) },
                  { label: "Respondidos em até 30 min", value: pct(report.response_times.within_30min_pct) },
                  { label: "Esperaram mais de 4 h", value: pct(report.response_times.over_4h_pct) },
                ].map((item) => (
                  <div key={item.label} className="border border-white/10 px-4 py-3">
                    <p className="text-xs uppercase tracking-wider text-muted">{item.label}</p>
                    <p className="mt-1 text-lg font-semibold text-lime">{item.value}</p>
                  </div>
                ))}
              </div>
              <p className="mt-4 text-xs font-semibold uppercase tracking-wider text-muted">Mensagens do cliente por hora do dia</p>
              <HourlyBarChart data={report.hourly_inbound} />
              <InsightList items={buildResponseInsights(report)} />
            </Section>

            {report.transfer_reasons.length > 0 && (
              <Section n={8} title="Motivos de transferência" subtitle="Top motivos registrados pela IA ao encaminhar">
                <HorizontalBarChart
                  items={report.transfer_reasons.map((r) => ({ label: r.reason, value: r.count }))}
                  maxItems={10}
                />
              </Section>
            )}

            <Section n={report.transfer_reasons.length > 0 ? 9 : 8} title="Próximos passos" subtitle="Ações sugeridas com base nos dados">
              <div className="overflow-x-auto">
                <table className="w-full min-w-[480px] text-left text-sm">
                  <thead>
                    <tr className="text-xs uppercase tracking-wider text-muted">
                      <th className="pb-2 pr-4">#</th>
                      <th className="pb-2 pr-4">Ação</th>
                      <th className="pb-2">Prioridade</th>
                    </tr>
                  </thead>
                  <tbody>
                    {buildActionItems(report).map((item, i) => (
                      <tr key={i} className="border-t border-white/10">
                        <td className="py-1.5 pr-4">{i + 1}</td>
                        <td className="py-1.5 pr-4">{item.action}</td>
                        <td className="py-1.5">{item.priority}</td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            </Section>

            <div className="report-section border-t border-white/10 pt-4 text-xs leading-relaxed text-sand/45">
              <p className="font-semibold uppercase tracking-wider text-muted">Notas metodológicas</p>
              <p className="mt-2">
                Base: conversas criadas entre {formatPeriod(report.period_from, report.period_to)} no canal IA (exclui Chat
                de teste). Cancelamentos contados por conversas com transferência por motivo de cancelamento. Unidades
                normalizadas para o nome cadastrado no catálogo do painel. Abandonos = conversa aberta na IA em que o
                cliente foi o último a escrever.
              </p>
            </div>
          </div>
        )}
      </div>
    </>
  );
}
