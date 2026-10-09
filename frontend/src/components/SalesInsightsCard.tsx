import { useEffect, useState } from "react";
import { api } from "../api";
import { SALES_STAGE_LABELS } from "../leadTags";

type Ranked = { value: string; label: string; count: number };

type SalesInsights = {
  stages: { stage: string; count: number }[];
  leads_in_funnel: number;
  students_identified: number;
  lost_reasons: Ranked[];
  topics: Ranked[];
  objections: Ranked[];
  units: Ranked[];
  plans: Ranked[];
  promotions: Ranked[];
};

type KnowledgeGap = {
  id: string;
  question: string;
  ai_reply: string;
  reason: string;
  created_at: string;
};

function RankedList({ title, items, empty }: { title: string; items: Ranked[]; empty: string }) {
  const max = Math.max(...items.map((i) => i.count), 1);
  return (
    <div>
      <p className="text-xs uppercase tracking-wider text-muted">{title}</p>
      {items.length === 0 ? (
        <p className="mt-2 text-sm text-sand/40">{empty}</p>
      ) : (
        <ul className="mt-2 space-y-1.5">
          {items.slice(0, 6).map((item) => (
            <li key={item.value} className="flex items-center gap-2 text-sm">
              <span className="w-36 shrink-0 truncate text-sand/70" title={item.label}>
                {item.label}
              </span>
              <div className="h-2 flex-1 rounded bg-white/5">
                <div className="h-2 rounded bg-leaf/70" style={{ width: `${(item.count / max) * 100}%` }} />
              </div>
              <span className="w-8 shrink-0 text-right font-semibold text-sand">{item.count}</span>
            </li>
          ))}
        </ul>
      )}
    </div>
  );
}

export default function SalesInsightsCard() {
  const [days, setDays] = useState<number | "all">(30);
  const [insights, setInsights] = useState<SalesInsights | null>(null);
  const [gaps, setGaps] = useState<KnowledgeGap[]>([]);
  const [error, setError] = useState("");

  useEffect(() => {
    const qs = days === "all" ? "" : `?days=${days}`;
    Promise.all([
      api<SalesInsights>(`/metrics/sales-insights${qs}`),
      api<KnowledgeGap[]>(`/metrics/knowledge-gaps?days=${days === "all" ? 365 : days}`),
    ])
      .then(([i, g]) => {
        setInsights(i);
        setGaps(g);
      })
      .catch((e) => setError(e.message));
  }, [days]);

  if (error) return <p className="text-ember">{error}</p>;
  if (!insights) return null;

  const stageMax = Math.max(...insights.stages.map((s) => s.count), 1);

  return (
    <div className="space-y-4">
      <div className="border border-white/10 bg-panel px-5 py-4">
        <div className="flex flex-wrap items-start justify-between gap-3">
          <div>
            <p className="text-xs uppercase tracking-wider text-muted">Funil de vendas da IA</p>
            <p className="mt-1 text-sm text-sand/50">
              Onde está cada lead que perguntou de planos ou promoção, e o que as conversas mostram —
              preenchido automaticamente a cada atendimento.
            </p>
          </div>
          <select
            className="rounded-md border border-white/15 bg-ink px-2 py-1.5 text-sm"
            value={days}
            onChange={(e) => setDays(e.target.value === "all" ? "all" : Number(e.target.value))}
          >
            <option value={7}>Últimos 7 dias</option>
            <option value={30}>Últimos 30 dias</option>
            <option value={90}>Últimos 90 dias</option>
            <option value="all">Todo o período</option>
          </select>
        </div>

        <div className="mt-4 space-y-2">
          {insights.stages.map((s) => (
            <div key={s.stage} className="flex items-center gap-3">
              <span className="w-28 shrink-0 text-sm text-sand/70">{SALES_STAGE_LABELS[s.stage] || s.stage}</span>
              <div className="h-5 flex-1 rounded bg-white/5">
                <div
                  className={`h-5 rounded transition-all ${
                    s.stage === "perdido" ? "bg-white/25" : s.stage === "matriculado" ? "bg-emerald-500/80" : "bg-leaf"
                  }`}
                  style={{ width: s.count ? `${Math.max((s.count / stageMax) * 100, 4)}%` : "0%" }}
                />
              </div>
              <span className="w-8 shrink-0 text-right text-sm font-semibold text-sand">{s.count}</span>
            </div>
          ))}
        </div>
        <p className="mt-3 text-xs text-sand/45">
          "Matriculado" hoje só é marcado manualmente na tela Clientes — a IA ainda não tem como
          confirmar a matrícula.
          {insights.students_identified > 0 &&
            ` ${insights.students_identified} lead(s) do funil foram depois identificados como alunos pelo CPF.`}
        </p>

        <div className="mt-5 grid gap-5 sm:grid-cols-2 lg:grid-cols-3">
          <RankedList title="Objeções" items={insights.objections} empty="Nenhuma objeção registrada." />
          <RankedList title="Motivos de perda" items={insights.lost_reasons} empty="Nenhum lead perdido." />
          <RankedList title="Assuntos" items={insights.topics} empty="Sem dados ainda." />
          <RankedList title="Unidades de interesse" items={insights.units} empty="Sem dados ainda." />
          <RankedList title="Planos de interesse" items={insights.plans} empty="Sem dados ainda." />
          <RankedList title="Promoções citadas" items={insights.promotions} empty="Sem dados ainda." />
        </div>
      </div>

      <div className="border border-white/10 bg-panel px-5 py-4">
        <p className="text-xs uppercase tracking-wider text-muted">Perguntas que a IA não soube responder</p>
        <p className="mt-1 text-sm text-sand/50">
          O que falta cadastrar em Unidades &amp; Planos ou na base de conhecimento — não conta Chat de teste.
        </p>
        {gaps.length === 0 ? (
          <p className="mt-4 text-sand/45">Nenhuma pergunta sem resposta nesse período.</p>
        ) : (
          <ul className="mt-4 max-h-80 space-y-3 overflow-auto">
            {gaps.map((g) => (
              <li key={g.id} className="border-t border-white/10 pt-3 text-sm first:border-t-0 first:pt-0">
                <p className="text-sand">“{g.question}”</p>
                <p className="mt-1 line-clamp-2 text-xs text-sand/45">IA: {g.ai_reply}</p>
                <p className="mt-1 text-xs text-sand/35">
                  {new Date(g.created_at).toLocaleString("pt-BR", {
                    day: "2-digit",
                    month: "2-digit",
                    hour: "2-digit",
                    minute: "2-digit",
                  })}
                  {g.reason === "modalidade_fora_do_catalogo" && " · modalidade fora do catálogo"}
                </p>
              </li>
            ))}
          </ul>
        )}
      </div>
    </div>
  );
}
