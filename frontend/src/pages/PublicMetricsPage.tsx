import { useEffect, useState } from "react";
import { useSearchParams } from "react-router-dom";
import { apiExternal } from "../api";

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

type StageCount = { stage: string; count: number };

type ToolStats = {
  tool_key: string;
  tool_name: string;
  total_calls: number;
  success_calls: number;
  failed_calls: number;
  distinct_conversations: number;
  success_rate: number | null;
};

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

const STORAGE_KEY = "movfit_public_metrics_key";

export default function PublicMetricsPage() {
  const [searchParams] = useSearchParams();
  const [key, setKey] = useState(() => searchParams.get("key") || localStorage.getItem(STORAGE_KEY) || "");
  const [keyInput, setKeyInput] = useState("");
  const [overview, setOverview] = useState<Overview | null>(null);
  const [funnel, setFunnel] = useState<StageCount[]>([]);
  const [tools, setTools] = useState<ToolStats[]>([]);
  const [error, setError] = useState("");
  const [loading, setLoading] = useState(false);

  useEffect(() => {
    if (searchParams.get("key")) {
      localStorage.setItem(STORAGE_KEY, searchParams.get("key")!);
    }
  }, [searchParams]);

  useEffect(() => {
    if (!key) return;
    setLoading(true);
    setError("");
    // Sem "/api" aqui: essa chamada já passa por API_URL, que em produção
    // já inclui "/api" (mesmo mecanismo do resto do painel) — repetir
    // duplicaria o prefixo. Sistemas externos (n8n) continuam usando
    // /api/v1/... normalmente, ver backend/app/routers/public_api.py.
    Promise.all([
      apiExternal<Overview>("/v1/metrics/overview", key),
      apiExternal<StageCount[]>("/v1/metrics/leads-funnel", key),
      apiExternal<ToolStats[]>("/v1/metrics/tools", key),
    ])
      .then(([o, f, t]) => {
        setOverview(o);
        setFunnel(f);
        setTools(t);
      })
      .catch((e) => {
        setError(e instanceof Error ? e.message : "Chave inválida ou expirada");
        localStorage.removeItem(STORAGE_KEY);
      })
      .finally(() => setLoading(false));
  }, [key]);

  const onSubmitKey = (e: React.FormEvent) => {
    e.preventDefault();
    if (!keyInput.trim()) return;
    setKey(keyInput.trim());
  };

  if (!key) {
    return (
      <div className="grid min-h-screen place-items-center bg-mesh px-4 text-sand">
        <form onSubmit={onSubmitKey} className="w-full max-w-sm space-y-4 border border-white/10 bg-panel p-6">
          <div>
            <p className="font-display text-2xl font-bold text-lime">Métricas da IA</p>
            <p className="mt-1 text-sm text-sand/55">Cola a chave de acesso que te enviaram.</p>
          </div>
          <input
            type="password"
            className="w-full rounded-md border border-white/15 bg-ink px-3 py-2"
            placeholder="mvk_..."
            value={keyInput}
            onChange={(e) => setKeyInput(e.target.value)}
            autoFocus
          />
          <button type="submit" className="w-full rounded-md bg-leaf px-4 py-2.5 font-semibold text-white hover:bg-lime">
            Ver métricas
          </button>
        </form>
      </div>
    );
  }

  const cards = overview
    ? [
        { label: "Conversas", value: overview.conversations_total },
        { label: "Msgs entrada", value: overview.messages_inbound },
        { label: "Msgs saída", value: overview.messages_outbound },
        {
          label: "Resolução IA",
          value: overview.ai_resolution_rate != null ? `${Math.round(overview.ai_resolution_rate * 100)}%` : "—",
        },
        { label: "Alunos", value: overview.students_total },
        { label: "Transferidos", value: overview.transferred_total },
        { label: "Pediram cancelamento", value: overview.cancellation_requests_total },
      ]
    : [];

  const known = STAGE_ORDER.filter((s) => funnel.some((f) => f.stage === s));
  const unknown = funnel
    .filter((f) => !STAGE_ORDER.includes(f.stage))
    .sort((a, b) => b.count - a.count)
    .map((f) => f.stage);
  const orderedStages = [...known, ...unknown];
  const maxCount = Math.max(1, ...funnel.map((f) => f.count));

  return (
    <div className="min-h-screen bg-mesh text-sand">
      <div className="pointer-events-none fixed inset-0 overflow-hidden">
        <div className="absolute -left-24 top-24 h-72 w-72 rounded-full bg-leaf/20 blur-3xl animate-pulse-soft" />
      </div>
      <div className="relative z-10 mx-auto max-w-5xl space-y-8 px-6 py-10">
        <div>
          <p className="font-display text-2xl font-extrabold tracking-tight text-lime">MOV FIT IA</p>
          <h2 className="mt-1 font-display text-3xl font-bold">Métricas da IA</h2>
          <p className="mt-1 text-sand/55">Só leitura — atualiza sozinho a cada visita.</p>
        </div>

        {error && <p className="text-ember">{error}</p>}
        {loading && !overview && <p className="text-sand/50">Carregando…</p>}

        {overview && (
          <>
            <div className="grid gap-4 sm:grid-cols-2 lg:grid-cols-4">
              {cards.map((c) => (
                <div key={c.label} className="border-l-2 border-leaf bg-panel px-5 py-4">
                  <p className="text-xs uppercase tracking-wider text-muted">{c.label}</p>
                  <p className="mt-2 font-display text-3xl font-bold text-lime">{c.value}</p>
                </div>
              ))}
            </div>

            <div className="grid gap-4 lg:grid-cols-2">
              <div className="border border-white/10 bg-panel px-5 py-4">
                <p className="text-xs uppercase tracking-wider text-muted">Funil de clientes por estágio</p>
                <div className="mt-4 space-y-2">
                  {orderedStages.length === 0 && <p className="text-sand/45">Nenhum cliente cadastrado ainda.</p>}
                  {orderedStages.map((s) => {
                    const entry = funnel.find((f) => f.stage === s);
                    if (!entry) return null;
                    const pct = (entry.count / maxCount) * 100;
                    return (
                      <div key={s} className="flex items-center gap-3">
                        <span className="w-28 shrink-0 text-sm text-sand/70">{STAGE_LABELS[s] || s}</span>
                        <div className="h-5 flex-1 rounded bg-white/5">
                          <div className="h-5 rounded bg-leaf transition-all" style={{ width: `${Math.max(pct, 4)}%` }} />
                        </div>
                        <span className="w-8 shrink-0 text-right text-sm font-semibold text-lime">{entry.count}</span>
                      </div>
                    );
                  })}
                </div>
              </div>

              <div className="border border-white/10 bg-panel px-5 py-4">
                <p className="text-xs uppercase tracking-wider text-muted">Uso por ferramenta</p>
                {tools.length === 0 ? (
                  <p className="mt-4 text-sand/45">Nenhuma ferramenta foi chamada em conversas reais ainda.</p>
                ) : (
                  <div className="mt-4 overflow-x-auto">
                    <table className="w-full min-w-[420px] text-left text-sm">
                      <thead>
                        <tr className="text-xs uppercase tracking-wider text-muted">
                          <th className="pb-2 pr-4">Ferramenta</th>
                          <th className="pb-2 pr-4">Conversas</th>
                          <th className="pb-2">Sucesso</th>
                        </tr>
                      </thead>
                      <tbody>
                        {tools.map((t) => (
                          <tr key={t.tool_key} className="border-t border-white/10">
                            <td className="py-2 pr-4">{t.tool_name}</td>
                            <td className="py-2 pr-4">{t.distinct_conversations}</td>
                            <td className="py-2 text-lime">
                              {t.success_rate != null ? `${Math.round(t.success_rate * 100)}%` : "—"}
                            </td>
                          </tr>
                        ))}
                      </tbody>
                    </table>
                  </div>
                )}
              </div>
            </div>
          </>
        )}
      </div>
    </div>
  );
}
