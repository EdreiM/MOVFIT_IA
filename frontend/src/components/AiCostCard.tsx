import { FormEvent, useCallback, useEffect, useState } from "react";
import { api } from "../api";
import { useAuth } from "../auth";

type Money = { usd: number; brl: number };

type AiCost = {
  month: string | null;
  usd_brl_rate: number;
  usd_brl_rate_auto: boolean;
  usd_brl_rate_updated_at: string | null;
  total: Money;
  test_chat: Money;
  calls: number;
  prompt_tokens: number;
  completion_tokens: number;
  calls_without_price: number;
  customers_count: number;
  average_per_customer: Money;
  by_month: (Money & { month: string; calls: number })[];
  by_model: (Money & { model: string; calls: number })[];
  customers: (Money & { phone: string | null; name: string | null; calls: number; last_at: string | null })[];
};

const brl = (value: number) => value.toLocaleString("pt-BR", { style: "currency", currency: "BRL" });
const usd = (value: number) =>
  value.toLocaleString("en-US", { style: "currency", currency: "USD", maximumFractionDigits: 4 });

function monthLabel(month: string): string {
  const [year, m] = month.split("-").map(Number);
  const label = new Date(year, m - 1, 1).toLocaleDateString("pt-BR", { month: "long", year: "numeric" });
  return label.charAt(0).toUpperCase() + label.slice(1);
}

function currentMonth(): string {
  const now = new Date();
  return `${now.getFullYear()}-${String(now.getMonth() + 1).padStart(2, "0")}`;
}

export default function AiCostCard() {
  const { companyId } = useAuth();
  const [period, setPeriod] = useState<string>(currentMonth()); // "AAAA-MM" ou "all"
  const [cost, setCost] = useState<AiCost | null>(null);
  const [rateInput, setRateInput] = useState("");
  const [error, setError] = useState("");

  const load = useCallback(() => {
    api<AiCost>(`/metrics/ai-cost${period === "all" ? "" : `?month=${period}`}`)
      .then((c) => {
        setCost(c);
        setRateInput(String(c.usd_brl_rate).replace(".", ","));
      })
      .catch((e) => setError(e.message));
  }, [period]);

  useEffect(() => {
    load();
  }, [load]);

  const saveRate = async (e: FormEvent) => {
    e.preventDefault();
    const rate = Number(rateInput.replace(",", "."));
    if (!companyId || !Number.isFinite(rate) || rate <= 0) {
      setError("Informe uma cotação válida (ex: 5,20).");
      return;
    }
    setError("");
    try {
      await api(`/ai-configs/${companyId}`, {
        method: "PATCH",
        body: JSON.stringify({ usd_brl_rate: rate, usd_brl_rate_auto: false }),
      });
      load();
    } catch (err) {
      setError(err instanceof Error ? err.message : "Erro ao salvar a cotação");
    }
  };

  const setAutoRate = async (auto: boolean) => {
    if (!companyId) return;
    setError("");
    try {
      await api(`/ai-configs/${companyId}`, { method: "PATCH", body: JSON.stringify({ usd_brl_rate_auto: auto }) });
      load();
    } catch (err) {
      setError(err instanceof Error ? err.message : "Erro ao salvar a cotação");
    }
  };

  if (!cost) return error ? <p className="text-ember">{error}</p> : null;

  // O mês atual sempre aparece no seletor, mesmo ainda sem nenhum registro.
  const months = Array.from(new Set([...cost.by_month.map((m) => m.month), currentMonth()])).sort().reverse();
  const monthMax = Math.max(...cost.by_month.map((m) => m.brl), 0.01);
  const neverRecorded = cost.by_month.length === 0;

  return (
    <div className="border border-white/10 bg-panel px-5 py-4">
      <div className="flex flex-wrap items-start justify-between gap-3">
        <div>
          <p className="text-xs uppercase tracking-wider text-muted">Custo da IA</p>
          <p className="mt-1 text-sm text-sand/50">
            Quanto a OpenAI cobrou pelas respostas, calculado pelos tokens de cada chamada e convertido
            pela cotação abaixo.
          </p>
        </div>
        <select
          className="rounded-md border border-white/15 bg-ink px-2 py-1.5 text-sm"
          value={period}
          onChange={(e) => setPeriod(e.target.value)}
        >
          {months.map((m) => (
            <option key={m} value={m}>
              {monthLabel(m)}
            </option>
          ))}
          <option value="all">Geral (todo o período)</option>
        </select>
      </div>
      {error && <p className="mt-2 text-sm text-ember">{error}</p>}

      {neverRecorded ? (
        <p className="mt-4 text-sand/45">
          Ainda não há registros. O custo passa a ser medido a partir desta versão — conversas
          anteriores não entram.
        </p>
      ) : (
        <>
          <div className="mt-4 grid gap-3 sm:grid-cols-3">
            <div className="border border-white/10 px-4 py-3">
              <p className="text-xs uppercase tracking-wider text-muted">Total</p>
              <p className="mt-1 font-display text-2xl font-bold text-sand">{brl(cost.total.brl)}</p>
              <p className="text-xs text-sand/45">{usd(cost.total.usd)}</p>
            </div>
            <div className="border border-white/10 px-4 py-3">
              <p className="text-xs uppercase tracking-wider text-muted">Custo médio por cliente</p>
              <p className="mt-1 font-display text-2xl font-bold text-sand">
                {brl(cost.average_per_customer.brl)}
              </p>
              <p className="text-xs text-sand/45">
                {cost.customers_count} cliente{cost.customers_count === 1 ? "" : "s"} atendido
                {cost.customers_count === 1 ? "" : "s"}
              </p>
            </div>
            <div className="border border-white/10 px-4 py-3">
              <p className="text-xs uppercase tracking-wider text-muted">Uso</p>
              <p className="mt-1 font-display text-2xl font-bold text-sand">
                {cost.calls.toLocaleString("pt-BR")}
              </p>
              <p className="text-xs text-sand/45">
                chamadas · {(cost.prompt_tokens + cost.completion_tokens).toLocaleString("pt-BR")} tokens
              </p>
            </div>
          </div>
          <p className="mt-2 text-xs text-sand/45">
            {cost.test_chat.usd > 0 && `Inclui ${brl(cost.test_chat.brl)} de Chat de teste. `}
            {cost.by_model.length > 0 &&
              `Modelos: ${cost.by_model.map((m) => `${m.model} (${brl(m.brl)})`).join(", ")}. `}
            {cost.calls_without_price > 0 &&
              `${cost.calls_without_price} chamada(s) de modelo sem preço cadastrado ficaram fora do valor.`}
          </p>

          <div className="mt-5 grid gap-6 lg:grid-cols-[1fr_1.4fr]">
            <div>
              <p className="text-xs uppercase tracking-wider text-muted">Por mês</p>
              <ul className="mt-2 space-y-1.5">
                {[...cost.by_month].reverse().map((m) => (
                  <li key={m.month} className="flex items-center gap-2 text-sm">
                    <span className="w-32 shrink-0 text-sand/70">{monthLabel(m.month)}</span>
                    <div className="h-2 flex-1 rounded bg-white/5">
                      <div className="h-2 rounded bg-leaf/70" style={{ width: `${(m.brl / monthMax) * 100}%` }} />
                    </div>
                    <span className="w-24 shrink-0 text-right font-semibold text-sand">{brl(m.brl)}</span>
                  </li>
                ))}
              </ul>
            </div>

            <div>
              <p className="text-xs uppercase tracking-wider text-muted">
                Custo por cliente <span className="normal-case text-sand/40">(os 50 maiores do período)</span>
              </p>
              {cost.customers.length === 0 ? (
                <p className="mt-2 text-sm text-sand/40">Nenhum atendimento real nesse período.</p>
              ) : (
                <div className="mt-2 max-h-72 overflow-auto">
                  <table className="w-full text-left text-sm">
                    <thead>
                      <tr className="text-xs uppercase tracking-wider text-muted">
                        <th className="pb-2 pr-3">Cliente</th>
                        <th className="pb-2 pr-3 text-right">Chamadas</th>
                        <th className="pb-2 text-right">Custo</th>
                      </tr>
                    </thead>
                    <tbody>
                      {cost.customers.map((c, i) => (
                        <tr key={c.phone ?? i} className="border-t border-white/10">
                          <td className="py-1.5 pr-3">
                            <p className="text-sand">{c.name || c.phone}</p>
                            {c.name && <p className="text-xs text-sand/45">{c.phone}</p>}
                          </td>
                          <td className="py-1.5 pr-3 text-right text-sand/70">{c.calls}</td>
                          <td className="py-1.5 text-right font-semibold text-sand">{brl(c.brl)}</td>
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

      <form onSubmit={saveRate} className="mt-5 flex flex-wrap items-center gap-x-3 gap-y-2 border-t border-white/10 pt-3 text-sm">
        <label className="flex items-center gap-2 text-sand/70">
          <input type="checkbox" checked={cost.usd_brl_rate_auto} onChange={(e) => setAutoRate(e.target.checked)} />
          Cotação do dólar automática (PTAX do Banco Central)
        </label>
        {cost.usd_brl_rate_auto ? (
          <span className="text-sand">
            {cost.usd_brl_rate.toLocaleString("pt-BR", {
              style: "currency",
              currency: "BRL",
              minimumFractionDigits: 4,
            })}
            <span className="ml-2 text-xs text-sand/40">
              {cost.usd_brl_rate_updated_at
                ? `atualizada em ${new Date(cost.usd_brl_rate_updated_at).toLocaleString("pt-BR", {
                    day: "2-digit",
                    month: "2-digit",
                    hour: "2-digit",
                    minute: "2-digit",
                  })}`
                : "ainda não foi possível buscar — usando a última cotação guardada"}
            </span>
          </span>
        ) : (
          <>
            <label className="text-sand/60" htmlFor="usd-brl-rate">
              Cotação usada (R$)
            </label>
            <input
              id="usd-brl-rate"
              className="w-24 rounded-md border border-white/15 bg-ink px-2 py-1.5"
              inputMode="decimal"
              value={rateInput}
              onChange={(e) => setRateInput(e.target.value)}
            />
            <button type="submit" className="rounded-md border border-white/15 px-3 py-1.5 text-sand/70 hover:bg-white/5">
              Salvar
            </button>
          </>
        )}
        <span className="basis-full text-xs text-sand/40">
          A OpenAI cobra em dólar; o valor em reais usa a cotação oficial do Banco Central. Desmarque se
          preferir digitar outra cotação.
        </span>
      </form>
    </div>
  );
}
