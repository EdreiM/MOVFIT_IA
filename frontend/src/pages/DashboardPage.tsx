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
};

type Health = {
  status: string;
  database: boolean;
  integrations_active: number;
  rag_sources_active: number;
  llm_configured: boolean;
};

export default function DashboardPage() {
  const [overview, setOverview] = useState<Overview | null>(null);
  const [health, setHealth] = useState<Health | null>(null);
  const [error, setError] = useState("");

  useEffect(() => {
    Promise.all([
      api<Overview>("/metrics/overview"),
      api<Health>("/admin/health"),
    ])
      .then(([o, h]) => {
        setOverview(o);
        setHealth(h);
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
