import { useEffect, useState } from "react";
import { api } from "../api";

type Overview = {
  conversations_total: number;
  messages_inbound: number;
  messages_outbound: number;
  ai_resolved: number;
  human_resolved: number;
  avg_response_seconds: number | null;
  ai_resolution_rate: number | null;
  students_total: number;
  transferred_total: number;
  cancellation_requests_total: number;
};

type Health = {
  status: string;
  database: boolean;
  integrations_active: number;
  rag_sources_active: number;
  llm_configured: boolean;
};

type MetricsPoint = {
  day: string;
  conversations_total: number;
  messages_inbound: number;
  messages_outbound: number;
};

type StageCount = {
  stage: string;
  count: number;
};

type ToolStats = {
  tool_key: string;
  tool_name: string;
  total_calls: number;
  success_calls: number;
  failed_calls: number;
  distinct_conversations: number;
  success_rate: number | null;
};

// Ordem canônica do funil — estágios conhecidos aparecem nessa ordem;
// qualquer estágio customizado que a IA ou um usuário crie entra depois,
// ordenado por volume.
const STAGE_ORDER = ["novo", "qualificado", "interessado", "transferido", "matriculado", "resolvido", "perdido"];
const STAGE_LABELS: Record<string, string> = {
  novo: "Novo",
  qualificado: "Qualificado",
  interessado: "Interessado",
  transferido: "Transferido",
  matriculado: "Matriculado",
  resolvido: "Resolvido",
  perdido: "Perdido",
};

function TrendChart({ points }: { points: MetricsPoint[] }) {
  if (points.length === 0) {
    return <p className="text-sand/45">Sem dados suficientes ainda — volta aqui depois de alguns dias de uso.</p>;
  }

  const width = 640;
  const height = 200;
  const padding = { top: 10, right: 10, bottom: 24, left: 10 };
  const innerW = width - padding.left - padding.right;
  const innerH = height - padding.top - padding.bottom;

  const series = [
    { key: "conversations_total" as const, label: "Conversas", color: "#7fe37a" },
    { key: "messages_inbound" as const, label: "Msgs entrada", color: "#5fb8ff" },
    { key: "messages_outbound" as const, label: "Msgs saída", color: "#ff8a5f" },
  ];

  const maxY = Math.max(1, ...points.flatMap((p) => series.map((s) => p[s.key])));
  const stepX = points.length > 1 ? innerW / (points.length - 1) : 0;

  const pathFor = (key: keyof MetricsPoint) =>
    points
      .map((p, i) => {
        const x = padding.left + i * stepX;
        const y = padding.top + innerH - (Number(p[key]) / maxY) * innerH;
        return `${i === 0 ? "M" : "L"}${x.toFixed(1)},${y.toFixed(1)}`;
      })
      .join(" ");

  const labelEvery = Math.max(1, Math.ceil(points.length / 6));

  return (
    <div>
      <svg viewBox={`0 0 ${width} ${height}`} className="w-full" role="img" aria-label="Tendência de conversas e mensagens">
        {series.map((s) => (
          <path key={s.key} d={pathFor(s.key)} fill="none" stroke={s.color} strokeWidth={2} />
        ))}
        {points.map((p, i) => {
          if (i % labelEvery !== 0 && i !== points.length - 1) return null;
          const x = padding.left + i * stepX;
          const [, m, d] = p.day.split("-");
          return (
            <text key={p.day} x={x} y={height - 6} fontSize={10} fill="#9a9a9a" textAnchor="middle">
              {d}/{m}
            </text>
          );
        })}
      </svg>
      <div className="mt-2 flex flex-wrap gap-4 text-xs">
        {series.map((s) => (
          <span key={s.key} className="flex items-center gap-1.5 text-sand/60">
            <span className="inline-block h-2 w-2 rounded-full" style={{ backgroundColor: s.color }} />
            {s.label}
          </span>
        ))}
      </div>
    </div>
  );
}

function StageFunnel({ stages }: { stages: StageCount[] }) {
  if (stages.length === 0) {
    return <p className="text-sand/45">Nenhum cliente cadastrado ainda.</p>;
  }
  const known = STAGE_ORDER.filter((s) => stages.some((st) => st.stage === s));
  const unknown = stages
    .filter((st) => !STAGE_ORDER.includes(st.stage))
    .sort((a, b) => b.count - a.count)
    .map((st) => st.stage);
  const orderedKeys = [...known, ...unknown];
  const maxCount = Math.max(1, ...stages.map((s) => s.count));

  return (
    <div className="space-y-2">
      {orderedKeys.map((key) => {
        const entry = stages.find((s) => s.stage === key);
        if (!entry) return null;
        const pct = (entry.count / maxCount) * 100;
        return (
          <div key={key} className="flex items-center gap-3">
            <span className="w-28 shrink-0 text-sm text-sand/70">{STAGE_LABELS[key] || key}</span>
            <div className="h-5 flex-1 rounded bg-white/5">
              <div
                className="h-5 rounded bg-leaf transition-all"
                style={{ width: `${Math.max(pct, 4)}%` }}
              />
            </div>
            <span className="w-8 shrink-0 text-right text-sm font-semibold text-lime">{entry.count}</span>
          </div>
        );
      })}
    </div>
  );
}

export default function DashboardPage() {
  const [overview, setOverview] = useState<Overview | null>(null);
  const [health, setHealth] = useState<Health | null>(null);
  const [timeseries, setTimeseries] = useState<MetricsPoint[]>([]);
  const [funnel, setFunnel] = useState<StageCount[]>([]);
  const [toolStats, setToolStats] = useState<ToolStats[]>([]);
  const [featuredTools, setFeaturedTools] = useState<ToolStats[]>([]);
  const [error, setError] = useState("");

  useEffect(() => {
    Promise.all([
      api<Overview>("/metrics/overview"),
      api<Health>("/admin/health"),
      api<MetricsPoint[]>("/metrics/timeseries"),
      api<StageCount[]>("/metrics/leads-funnel"),
      api<ToolStats[]>("/metrics/tools"),
      api<ToolStats[]>("/metrics/featured-tools"),
    ])
      .then(([o, h, ts, f, t, ft]) => {
        setOverview(o);
        setHealth(h);
        setTimeseries(ts);
        setFunnel(f);
        setToolStats(t);
        setFeaturedTools(ft);
      })
      .catch((e) => setError(e.message));
  }, []);

  const cards = overview
    ? [
        { label: "Conversas", value: overview.conversations_total },
        { label: "Msgs entrada", value: overview.messages_inbound },
        { label: "Msgs saída", value: overview.messages_outbound },
        {
          label: "Resolução IA",
          value:
            overview.ai_resolution_rate != null
              ? `${Math.round(overview.ai_resolution_rate * 100)}%`
              : "—",
        },
        { label: "Alunos", value: overview.students_total },
        { label: "Transferidos", value: overview.transferred_total },
        { label: "Pediram cancelamento", value: overview.cancellation_requests_total },
        ...featuredTools.map((t) => ({ label: t.tool_name, value: t.success_calls })),
      ]
    : [];

  return (
    <div className="space-y-8">
      <div>
        <h2 className="font-display text-3xl font-bold text-sand">Painel</h2>
        <p className="mt-1 text-sand/55">Visão geral do atendimento.</p>
      </div>

      {error && <p className="text-ember">{error}</p>}

      <div className="grid gap-4 sm:grid-cols-2 lg:grid-cols-4">
        {cards.map((c) => (
          <div
            key={c.label}
            className="border-l-2 border-leaf bg-panel px-5 py-4"
          >
            <p className="text-xs uppercase tracking-wider text-muted">{c.label}</p>
            <p className="mt-2 font-display text-3xl font-bold text-lime">{c.value}</p>
          </div>
        ))}
      </div>

      <div className="grid gap-4 lg:grid-cols-2">
        <div className="border border-white/10 bg-panel px-5 py-4">
          <p className="text-xs uppercase tracking-wider text-muted">Tendência (últimos 30 dias)</p>
          <div className="mt-4">
            <TrendChart points={timeseries} />
          </div>
        </div>

        <div className="border border-white/10 bg-panel px-5 py-4">
          <p className="text-xs uppercase tracking-wider text-muted">Funil de clientes por estágio</p>
          <div className="mt-4">
            <StageFunnel stages={funnel} />
          </div>
        </div>
      </div>

      <div className="border border-white/10 bg-panel px-5 py-4">
        <p className="text-xs uppercase tracking-wider text-muted">Uso por ferramenta</p>
        <p className="mt-1 text-sm text-sand/50">
          Quantas conversas usaram cada ferramenta e a taxa de sucesso — não conta Chat de teste.
        </p>
        {toolStats.length === 0 ? (
          <p className="mt-4 text-sand/45">Nenhuma ferramenta foi chamada em conversas reais ainda.</p>
        ) : (
          <div className="mt-4 overflow-x-auto">
            <table className="w-full min-w-[560px] text-left text-sm">
              <thead>
                <tr className="text-xs uppercase tracking-wider text-muted">
                  <th className="pb-2 pr-4">Ferramenta</th>
                  <th className="pb-2 pr-4">Conversas</th>
                  <th className="pb-2 pr-4">Chamadas</th>
                  <th className="pb-2 pr-4">Sucesso</th>
                  <th className="pb-2">Falhas</th>
                </tr>
              </thead>
              <tbody>
                {toolStats.map((t) => (
                  <tr key={t.tool_key} className="border-t border-white/10">
                    <td className="py-2 pr-4">
                      <p className="font-medium text-sand">{t.tool_name}</p>
                      <p className="text-xs text-sand/45">{t.tool_key}</p>
                    </td>
                    <td className="py-2 pr-4">{t.distinct_conversations}</td>
                    <td className="py-2 pr-4">{t.total_calls}</td>
                    <td className="py-2 pr-4 text-lime">
                      {t.success_rate != null ? `${Math.round(t.success_rate * 100)}%` : "—"}
                    </td>
                    <td className="py-2 text-ember">{t.failed_calls || "—"}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
      </div>

      {health && (
        <div className="border border-white/10 bg-panel px-5 py-4">
          <p className="text-xs uppercase tracking-wider text-muted">Saúde do sistema</p>
          <ul className="mt-3 grid gap-2 text-sm sm:grid-cols-2">
            <li>Banco: {health.database ? "ok" : "erro"}</li>
            <li>Integrações ativas: {health.integrations_active}</li>
            <li>RAGs ativos: {health.rag_sources_active}</li>
            <li>LLM configurado: {health.llm_configured ? "sim" : "não — configure em Config. IA"}</li>
          </ul>
        </div>
      )}
    </div>
  );
}
