import { useEffect, useState } from "react";
import { api } from "../api";

type TransfersSummary = {
  period_days: number;
  total_transfers: number;
  by_category: { category: string; count: number }[];
  top_reasons: { reason: string; count: number }[];
};

export default function TransfersSummaryCard() {
  const [data, setData] = useState<TransfersSummary | null>(null);
  const [error, setError] = useState("");

  useEffect(() => {
    api<TransfersSummary>("/metrics/transfers-summary?days=30")
      .then(setData)
      .catch((e) => setError(e.message));
  }, []);

  if (error) {
    return (
      <div className="border border-ember/30 bg-panel px-5 py-4 text-sm text-ember">
        Não foi possível carregar transferências: {error}
      </div>
    );
  }

  if (!data) {
    return (
      <div className="border border-white/10 bg-panel px-5 py-4 text-sm text-sand/45">
        Carregando transferências…
      </div>
    );
  }

  const maxCat = Math.max(1, ...data.by_category.map((c) => c.count));

  return (
    <div className="border border-white/10 bg-panel px-5 py-4">
      <div className="flex flex-wrap items-baseline justify-between gap-2">
        <div>
          <p className="text-xs uppercase tracking-wider text-muted">Transferências para atendente</p>
          <p className="mt-1 text-sm text-sand/50">Últimos {data.period_days} dias — chat de teste não entra.</p>
        </div>
        <p className="font-display text-2xl font-bold text-lime">{data.total_transfers}</p>
      </div>

      {data.total_transfers === 0 ? (
        <p className="mt-4 text-sm text-sand/45">Nenhuma transferência registrada no período.</p>
      ) : (
        <div className="mt-5 grid gap-6 lg:grid-cols-2">
          <div>
            <p className="text-xs font-semibold uppercase tracking-wider text-sand/55">Por tipo</p>
            <ul className="mt-3 space-y-2">
              {data.by_category.map((row) => (
                <li key={row.category}>
                  <div className="flex justify-between text-sm">
                    <span className="text-sand/85">{row.category}</span>
                    <span className="text-sand/55">{row.count}</span>
                  </div>
                  <div className="mt-1 h-1.5 overflow-hidden rounded bg-white/10">
                    <div
                      className="h-full rounded bg-leaf/80"
                      style={{ width: `${Math.round((row.count / maxCat) * 100)}%` }}
                    />
                  </div>
                </li>
              ))}
            </ul>
          </div>
          <div>
            <p className="text-xs font-semibold uppercase tracking-wider text-sand/55">Motivos mais frequentes</p>
            <ul className="mt-3 max-h-48 space-y-2 overflow-auto text-sm">
              {data.top_reasons.map((row) => (
                <li key={row.reason} className="flex gap-2 border-b border-white/5 pb-2">
                  <span className="shrink-0 font-medium text-lime">{row.count}×</span>
                  <span className="text-sand/75">{row.reason}</span>
                </li>
              ))}
            </ul>
          </div>
        </div>
      )}
    </div>
  );
}
