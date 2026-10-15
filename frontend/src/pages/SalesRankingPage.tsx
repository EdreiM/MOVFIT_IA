import { useCallback, useEffect, useMemo, useState } from "react";
import { Link } from "react-router-dom";
import { api } from "../api";
import { useAuth } from "../auth";
import SalesCard from "../components/SalesCard";

type Person = {
  posicao: number;
  nome: string;
  nome_curto: string;
  novas_anuais: number;
  matriculas: number;
  estimated: boolean;
};

type SellerContract = {
  cliente: string;
  matricula: string | null;
  data: string;
  tipo: string;
  plano: string | null;
  duracao: number | null;
  unidade: string;
  conta: boolean;
  motivo: string | null;
};

type SellerResponse = { nome: string; total: number; contratos: SellerContract[]; disponivel: boolean };

type RankingResponse = {
  periodo: { de: string; ate: string };
  unidade: string;
  unidade_nome: string | null;
  unidades: { key: string; nome: string; nome_exibicao: string }[];
  total_novas_anuais: number;
  equipe: number;
  site: number;
  nao_identificado: number;
  matriculas_todas: number;
  ranking: Person[];
  aproximado: boolean;
  dias_pendentes: number;
  atualizado_em: string | null;
  fonte: "n8n" | "mcp" | "misto";
  conexao: string;
  conexao_erro: string | null;
  atualizando: boolean;
};

const pad = (n: number) => String(n).padStart(2, "0");
const iso = (d: Date) => `${d.getFullYear()}-${pad(d.getMonth() + 1)}-${pad(d.getDate())}`;
const br = (isoDate: string) => isoDate.split("-").reverse().join("/");

function shortcuts() {
  const today = new Date();
  const yesterday = new Date(today);
  yesterday.setDate(today.getDate() - 1);
  const week = new Date(today);
  week.setDate(today.getDate() - 6);
  const monthStart = new Date(today.getFullYear(), today.getMonth(), 1);
  const prevStart = new Date(today.getFullYear(), today.getMonth() - 1, 1);
  const prevEnd = new Date(today.getFullYear(), today.getMonth(), 0);
  return [
    { label: "Hoje", from: iso(today), to: iso(today) },
    { label: "Ontem", from: iso(yesterday), to: iso(yesterday) },
    { label: "Últimos 7 dias", from: iso(week), to: iso(today) },
    { label: "Este mês", from: iso(monthStart), to: iso(today) },
    { label: "Mês passado", from: iso(prevStart), to: iso(prevEnd) },
  ];
}

const medal = ["border-yellow-400/60 text-yellow-300", "border-slate-300/50 text-slate-200", "border-orange-400/50 text-orange-300"];

export default function SalesRankingPage() {
  const { user, companies, companyId } = useAuth();
  const canFixConnection = user?.role !== "vendas";
  const companyName = companies.find((c) => c.id === companyId)?.name;
  const presets = useMemo(shortcuts, []);
  const [unit, setUnit] = useState("all");
  const [from, setFrom] = useState(presets[3].from);
  const [to, setTo] = useState(presets[3].to);
  const [data, setData] = useState<RankingResponse | null>(null);
  const [error, setError] = useState("");
  const [loading, setLoading] = useState(true);
  const [refreshing, setRefreshing] = useState(false);
  const [openSeller, setOpenSeller] = useState<string | null>(null);
  const [sellerData, setSellerData] = useState<Record<string, SellerResponse | "loading" | "error">>({});

  // Mudou o filtro: as listas abertas deixam de valer.
  useEffect(() => {
    setOpenSeller(null);
    setSellerData({});
  }, [unit, from, to]);

  const toggleSeller = async (name: string) => {
    if (openSeller === name) {
      setOpenSeller(null);
      return;
    }
    setOpenSeller(name);
    if (sellerData[name] && sellerData[name] !== "error") return;
    setSellerData((d) => ({ ...d, [name]: "loading" }));
    try {
      const q = new URLSearchParams({ name, unit, date_from: from, date_to: to });
      const res = await api<SellerResponse>(`/sales/seller-contracts?${q.toString()}`);
      setSellerData((d) => ({ ...d, [name]: res }));
    } catch {
      setSellerData((d) => ({ ...d, [name]: "error" }));
    }
  };

  const load = useCallback(async () => {
    try {
      const q = new URLSearchParams({ unit, date_from: from, date_to: to });
      setData(await api<RankingResponse>(`/sales/ranking?${q.toString()}`));
      setError("");
    } catch (e) {
      setError((e as Error).message);
    } finally {
      setLoading(false);
    }
  }, [unit, from, to]);

  useEffect(() => {
    setLoading(true);
    load();
  }, [load]);

  // Enquanto o histórico carrega ou a atualização roda, confere a cada 8s;
  // depois, a cada minuto.
  useEffect(() => {
    const busy = refreshing || data?.atualizando || (data?.dias_pendentes ?? 0) > 0;
    const timer = setInterval(load, busy ? 8000 : 60000);
    return () => clearInterval(timer);
  }, [load, refreshing, data?.atualizando, data?.dias_pendentes]);

  const refreshNow = async () => {
    setRefreshing(true);
    try {
      await api("/sales/sync", { method: "POST" });
      await load();
    } catch (e) {
      setError((e as Error).message);
    } finally {
      setTimeout(() => setRefreshing(false), 4000);
    }
  };

  const max = data?.ranking[0]?.novas_anuais ?? 0;
  const unitName = data?.unidade_nome ?? data?.unidades.find((u) => u.key === unit)?.nome_exibicao;

  return (
    <div className="space-y-8">
      <div>
        <h2 className="font-display text-3xl font-bold">Ranking de vendas</h2>
        <p className="mt-1 text-sand/55">
          Novas matrículas <strong>anuais</strong> (matrícula e rematrícula) por responsável pelo
          lançamento. Renovação e planos que não são anuais não contam.
        </p>
      </div>

      {error && <p className="text-ember">{error}</p>}
      {data && data.conexao !== "connected" && (
        <div className="space-y-1 rounded-md border border-ember/40 px-4 py-3">
          <p className="font-medium text-ember">
            {data.conexao === "error"
              ? "O acesso à Pacto caiu — por isso os números não estão atualizando."
              : `A Pacto não está conectada${companyName ? ` na empresa "${companyName}"` : " neste painel"}.`}
          </p>
          {data.conexao_erro && <p className="text-sm text-sand/60">{data.conexao_erro}</p>}
          <p className="text-sm text-sand/70">
            {canFixConnection ? (
              <>
                Abra{" "}
                <Link to="/integrations" className="underline">
                  Integrações
                </Link>{" "}
                e clique em <strong>{data.conexao === "error" ? "Reconectar Pacto" : "Conectar Pacto"}</strong>.
                Se já conectou, confira se está na empresa certa (seletor no topo).
              </>
            ) : (
              "Peça a quem administra o painel para reconectar a Pacto."
            )}
          </p>
        </div>
      )}

      <div className="space-y-4">
        <div className="flex flex-wrap items-center gap-3">
          <select
            className="rounded-md border border-white/15 bg-ink px-3 py-2 text-sm"
            value={unit}
            onChange={(e) => setUnit(e.target.value)}
          >
            <option value="all">Todas as unidades</option>
            {data?.unidades.map((u) => (
              <option key={u.key} value={u.key}>
                {u.nome_exibicao}
              </option>
            ))}
          </select>
          <label className="flex items-center gap-2 text-sm text-sand/70">
            De
            <input
              type="date"
              value={from}
              max={to}
              onChange={(e) => e.target.value && setFrom(e.target.value)}
              className="rounded-md border border-white/15 bg-ink px-2 py-2 text-sm"
            />
          </label>
          <label className="flex items-center gap-2 text-sm text-sand/70">
            até
            <input
              type="date"
              value={to}
              min={from}
              onChange={(e) => e.target.value && setTo(e.target.value)}
              className="rounded-md border border-white/15 bg-ink px-2 py-2 text-sm"
            />
          </label>
        </div>
        <div className="flex flex-wrap gap-2">
          {presets.map((p) => {
            const active = p.from === from && p.to === to;
            return (
              <button
                key={p.label}
                type="button"
                onClick={() => {
                  setFrom(p.from);
                  setTo(p.to);
                }}
                className={`rounded-md border px-3 py-1.5 text-sm ${
                  active ? "border-leaf bg-leaf text-white" : "border-white/15 text-sand/70 hover:bg-white/5"
                }`}
              >
                {p.label}
              </button>
            );
          })}
        </div>
      </div>

      {loading && !data ? (
        <p className="text-sand/50">Carregando…</p>
      ) : (
        data && (
          <>
            <div className="grid gap-4 sm:grid-cols-3">
              <div className="border border-white/10 px-5 py-4">
                <p className="text-xs uppercase tracking-wider text-muted">Novas matrículas anuais</p>
                <p className="mt-1 font-display text-4xl font-bold text-lime">{data.total_novas_anuais}</p>
                <p className="mt-1 text-xs text-sand/45">
                  {unit === "all" ? "todas as unidades" : unitName} · {br(data.periodo.de)} a {br(data.periodo.ate)}
                </p>
              </div>
              <div className="border border-white/10 px-5 py-4">
                <p className="text-xs uppercase tracking-wider text-muted">Fechadas pela equipe</p>
                <p className="mt-1 font-display text-4xl font-bold">{data.equipe}</p>
              </div>
              <div className="border border-white/10 px-5 py-4">
                <p className="text-xs uppercase tracking-wider text-muted">Pelo site</p>
                <p className="mt-1 font-display text-4xl font-bold">{data.site}</p>
              </div>
            </div>

            <ul className="space-y-2">
              {data.ranking.map((p) => {
                const detail = sellerData[p.nome];
                const canOpen = data.fonte !== "mcp";
                return (
                  <li
                    key={p.nome}
                    className="relative overflow-hidden border border-white/10"
                    title={p.estimated ? "Valor aproximado: o dia misturou venda anual e não anual" : undefined}
                  >
                    <div
                      className="absolute inset-y-0 left-0 bg-leaf/15"
                      style={{ width: `${max ? (p.novas_anuais / max) * 100 : 0}%` }}
                    />
                    <div
                      className={`relative flex items-center gap-4 px-4 py-3 ${canOpen ? "cursor-pointer hover:bg-white/5" : ""}`}
                      role={canOpen ? "button" : undefined}
                      tabIndex={canOpen ? 0 : undefined}
                      aria-expanded={canOpen ? openSeller === p.nome : undefined}
                      onClick={canOpen ? () => toggleSeller(p.nome) : undefined}
                      onKeyDown={
                        canOpen
                          ? (e) => {
                              if (e.key === "Enter" || e.key === " ") {
                                e.preventDefault();
                                toggleSeller(p.nome);
                              }
                            }
                          : undefined
                      }
                    >
                      <span
                        className={`grid h-9 w-9 shrink-0 place-items-center rounded-full border font-display text-lg font-bold ${
                          medal[p.posicao - 1] ?? "border-white/15 text-sand/60"
                        }`}
                      >
                        {p.posicao}º
                      </span>
                      <span className="flex-1 font-medium">{p.nome}</span>
                      {p.estimated && <span className="text-xs text-sand/40">≈</span>}
                      <span className="font-display text-2xl font-bold">{p.novas_anuais}</span>
                      <span className="hidden text-xs text-sand/45 sm:inline">matrículas</span>
                      {canOpen && (
                        <span className="text-sand/40" aria-hidden>
                          {openSeller === p.nome ? "▲" : "▼"}
                        </span>
                      )}
                    </div>
                    {canOpen && openSeller === p.nome && (
                      <div className="relative border-t border-white/10 bg-black/20 px-4 py-3 text-sm">
                        {detail === "loading" || detail === undefined ? (
                          <p className="text-sand/50">Carregando…</p>
                        ) : detail === "error" ? (
                          <p className="text-ember">Não consegui carregar a lista. Tente de novo.</p>
                        ) : detail.contratos.length === 0 ? (
                          <p className="text-sand/50">Nenhum contrato encontrado nesse período.</p>
                        ) : (
                          <ul className="divide-y divide-white/5">
                            {detail.contratos.map((c, i) => (
                              <li
                                key={`${c.matricula}-${c.data}-${i}`}
                                className={`flex flex-wrap items-baseline gap-x-4 gap-y-1 py-2 ${c.conta ? "" : "opacity-60"}`}
                              >
                                <span className="w-24 shrink-0 text-sand/50">{br(c.data)}</span>
                                <span className="min-w-0 flex-1 font-medium">{c.cliente}</span>
                                <span className="text-sand/60">{c.tipo}</span>
                                <span className="text-sand/50">{c.plano}</span>
                                {!c.conta && (
                                  <span className="text-xs text-yellow-300">não conta: {c.motivo?.toLowerCase()}</span>
                                )}
                              </li>
                            ))}
                          </ul>
                        )}
                      </div>
                    )}
                  </li>
                );
              })}
              {data.ranking.length === 0 && (
                <li className="border border-white/10 px-4 py-8 text-center text-sand/45">
                  Nenhuma matrícula anual nesse período.
                </li>
              )}
            </ul>

            {data.ranking.length > 0 && (
              <section className="space-y-3 border-t border-white/10 pt-6">
                <h3 className="font-display text-xl font-semibold">Card para enviar</h3>
                <p className="text-sm text-sand/50">
                  O mesmo formato que a supervisão monta à mão. Baixe a imagem e envie no grupo.
                </p>
                <SalesCard
                  people={data.ranking}
                  total={data.total_novas_anuais}
                  equipe={data.equipe}
                  site={data.site}
                  unitLabel={data.unidade_nome ?? "Todas as unidades"}
                  scopeAll={data.unidade_nome === null}
                  from={data.periodo.de}
                  to={data.periodo.ate}
                />
              </section>
            )}

            {data.nao_identificado > 0 && (
              <p className="text-sm text-sand/50">
                + {data.nao_identificado} matrícula(s) que a Pacto contou mas não atribuiu a nenhum responsável.
              </p>
            )}

            <div className="flex flex-wrap items-center justify-between gap-3 border-t border-white/10 pt-4 text-sm text-sand/50">
              <div className="space-y-1">
                <p>
                  {data.atualizado_em
                    ? `Atualizado em ${new Date(data.atualizado_em).toLocaleString("pt-BR")}`
                    : "Ainda sem dados coletados."}
                </p>
                {data.dias_pendentes > 0 && (
                  <p className="text-yellow-300">
                    Carregando o histórico da Pacto — faltam {data.dias_pendentes} dia(s)/unidade neste período, os
                    números podem estar incompletos.
                  </p>
                )}
                {data.aproximado && (
                  <p>≈ Alguns valores são aproximados quando o mesmo dia teve venda anual e não anual.</p>
                )}
              </div>
              {data.fonte === "n8n" ? (
                <p className="text-xs text-sand/40">Atualiza sozinho a cada poucos minutos.</p>
              ) : (
                <button
                  type="button"
                  onClick={refreshNow}
                  disabled={refreshing || data.atualizando || data.conexao !== "connected"}
                  className="rounded-md border border-white/15 px-4 py-2 text-sand/80 hover:bg-white/5 disabled:opacity-50"
                >
                  {refreshing || data.atualizando ? "Atualizando…" : "Atualizar agora"}
                </button>
              )}
            </div>
          </>
        )
      )}
    </div>
  );
}
