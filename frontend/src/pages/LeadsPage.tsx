import { FormEvent, useEffect, useState } from "react";
import { api } from "../api";
import { useAuth } from "../auth";
import { LOST_REASON_LABELS, SALES_STAGE_LABELS, tagClass, tagLabel } from "../leadTags";

type Lead = {
  id: string;
  phone: string;
  name: string | null;
  cpf: string | null;
  email: string | null;
  birthdate: string | null;
  unit: string | null;
  stage: string;
  physical_eval_scheduled: boolean;
  last_physical_eval_date: string | null;
  last_physical_eval_time: string | null;
  custom_fields: Record<string, unknown>;
  tags: string[];
  sales_stage: string | null;
  lost_reason: string | null;
  updated_at: string;
};

function formatEvalDate(yyyyMMdd: string | null): string {
  if (!yyyyMMdd || yyyyMMdd.length !== 8) return "";
  return `${yyyyMMdd.slice(6, 8)}/${yyyyMMdd.slice(4, 6)}/${yyyyMMdd.slice(0, 4)}`;
}

export default function LeadsPage() {
  const { user } = useAuth();
  const isSuperAdmin = user?.role === "super_admin";
  const [items, setItems] = useState<Lead[]>([]);
  const [search, setSearch] = useState("");
  const [selected, setSelected] = useState<Lead | null>(null);
  const [form, setForm] = useState({
    name: "",
    cpf: "",
    email: "",
    birthdate: "",
    unit: "",
    stage: "",
    sales_stage: "",
  });
  const [salesStageFilter, setSalesStageFilter] = useState("");
  const [error, setError] = useState("");
  const [msg, setMsg] = useState("");

  const load = (q?: string) => {
    const query = q !== undefined ? q : search;
    const params = new URLSearchParams();
    if (query.trim()) params.set("q", query.trim());
    if (salesStageFilter) params.set("sales_stage", salesStageFilter);
    const qs = params.toString();
    api<Lead[]>(`/leads${qs ? `?${qs}` : ""}`)
      .then(setItems)
      .catch((e) => setError(e.message));
  };

  useEffect(() => {
    load();
  }, [salesStageFilter]);

  const onSearch = (e: FormEvent) => {
    e.preventDefault();
    load();
  };

  const open = (lead: Lead) => {
    setSelected(lead);
    setMsg("");
    setForm({
      name: lead.name ?? "",
      cpf: lead.cpf ?? "",
      email: lead.email ?? "",
      birthdate: lead.birthdate ?? "",
      unit: lead.unit ?? "",
      stage: lead.stage,
      sales_stage: lead.sales_stage ?? "",
    });
  };

  const save = async (e: FormEvent) => {
    e.preventDefault();
    if (!selected) return;
    setError("");
    setMsg("");
    try {
      const updated = await api<Lead>(`/leads/${selected.id}`, {
        method: "PATCH",
        body: JSON.stringify({
          name: form.name || null,
          cpf: form.cpf || null,
          email: form.email || null,
          birthdate: form.birthdate || null,
          unit: form.unit || null,
          stage: form.stage || null,
          sales_stage: form.sales_stage || null,
        }),
      });
      setSelected(updated);
      setMsg("Salvo.");
      load();
    } catch (err) {
      setError(err instanceof Error ? err.message : "Erro ao salvar");
    }
  };

  const onDelete = async () => {
    if (!selected) return;
    if (!confirm(`Excluir o cadastro de "${selected.name || selected.phone}"? Essa ação não pode ser desfeita.`))
      return;
    setError("");
    try {
      await api(`/leads/${selected.id}`, { method: "DELETE" });
      setSelected(null);
      load();
    } catch (err) {
      setError(err instanceof Error ? err.message : "Erro ao excluir");
    }
  };

  return (
    <div className="space-y-6">
      <div>
        <h2 className="font-display text-3xl font-bold">Clientes</h2>
        <p className="mt-1 text-sand/55">
          Cadastro estruturado de cada cliente (nome, CPF, e-mail, estágio) — a IA grava aqui o
          que aprende na conversa, sem depender do histórico de mensagens.
        </p>
      </div>
      {error && <p className="text-ember">{error}</p>}

      <div className="grid gap-4 lg:grid-cols-[360px_1fr]">
        <div className="space-y-2">
          <form onSubmit={onSearch} className="flex gap-2">
            <input
              className="w-full rounded-md border border-white/15 bg-ink px-3 py-2 text-sm"
              placeholder="Buscar por nome, telefone, CPF ou e-mail..."
              value={search}
              onChange={(e) => setSearch(e.target.value)}
            />
            <button type="submit" className="rounded-md border border-white/15 px-3 py-2 text-sm text-sand/70 hover:bg-white/5">
              Buscar
            </button>
          </form>
          <select
            className="w-full rounded-md border border-white/15 bg-ink px-2 py-2 text-sm"
            value={salesStageFilter}
            onChange={(e) => setSalesStageFilter(e.target.value)}
          >
            <option value="">Funil de vendas: todos</option>
            {Object.entries(SALES_STAGE_LABELS).map(([value, label]) => (
              <option key={value} value={value}>
                {label}
              </option>
            ))}
          </select>
          <ul className="max-h-[70vh] overflow-auto border border-white/10">
            {items.map((l) => (
              <li key={l.id}>
                <button
                  onClick={() => open(l)}
                  className={`w-full border-b border-white/10 px-4 py-3 text-left transition hover:bg-white/5 ${
                    selected?.id === l.id ? "bg-leaf/15 border-l-2 border-leaf" : ""
                  }`}
                >
                  <p className="font-medium">{l.name || l.phone}</p>
                  <p className="text-xs text-sand/50">
                    {l.phone} · {l.stage}
                    {l.sales_stage && ` · funil: ${SALES_STAGE_LABELS[l.sales_stage] || l.sales_stage}`}
                  </p>
                </button>
              </li>
            ))}
            {items.length === 0 && (
              <li className="px-4 py-8 text-center text-sand/45">Nenhum cliente cadastrado ainda.</li>
            )}
          </ul>
        </div>

        <div className="border border-white/10 bg-ink/30 p-5">
          {!selected ? (
            <p className="text-sand/45">Selecione um cliente.</p>
          ) : (
            <form onSubmit={save} className="space-y-4">
              <div className="flex items-center justify-between">
                <p className="text-sm text-sand/50">{selected.phone}</p>
                {isSuperAdmin && (
                  <button
                    type="button"
                    onClick={onDelete}
                    className="rounded-md border border-ember/40 px-3 py-1.5 text-sm text-ember hover:bg-ember/10"
                  >
                    Excluir
                  </button>
                )}
              </div>

              {selected.last_physical_eval_date && (
                <div className="rounded-md border border-leaf/30 bg-leaf/10 px-3 py-2 text-sm text-sand/80">
                  Última avaliação física agendada pela IA:{" "}
                  <span className="font-medium text-sand">
                    {formatEvalDate(selected.last_physical_eval_date)}
                    {selected.last_physical_eval_time ? ` às ${selected.last_physical_eval_time}` : ""}
                  </span>
                </div>
              )}

              {selected.tags.length > 0 && (
                <div>
                  <p className="text-sm text-sand/60">
                    Etiquetas <span className="text-sand/40">(preenchidas pela IA a cada atendimento)</span>
                  </p>
                  <div className="mt-1.5 flex flex-wrap gap-1.5">
                    {selected.tags.map((tag) => (
                      <span key={tag} className={`rounded border px-2 py-0.5 text-xs ${tagClass(tag)}`}>
                        {tagLabel(tag)}
                      </span>
                    ))}
                  </div>
                </div>
              )}

              <div className="grid gap-3 sm:grid-cols-2">
                <label className="block space-y-1">
                  <span className="text-sm text-sand/60">Nome</span>
                  <input
                    className="w-full rounded-md border border-white/15 bg-ink px-3 py-2"
                    value={form.name}
                    onChange={(e) => setForm({ ...form, name: e.target.value })}
                  />
                </label>
                <label className="block space-y-1">
                  <span className="text-sm text-sand/60">CPF</span>
                  <input
                    className="w-full rounded-md border border-white/15 bg-ink px-3 py-2"
                    value={form.cpf}
                    onChange={(e) => setForm({ ...form, cpf: e.target.value })}
                  />
                </label>
                <label className="block space-y-1">
                  <span className="text-sm text-sand/60">E-mail</span>
                  <input
                    className="w-full rounded-md border border-white/15 bg-ink px-3 py-2"
                    value={form.email}
                    onChange={(e) => setForm({ ...form, email: e.target.value })}
                  />
                </label>
                <label className="block space-y-1">
                  <span className="text-sm text-sand/60">Data de nascimento</span>
                  <input
                    type="date"
                    className="w-full rounded-md border border-white/15 bg-ink px-3 py-2"
                    value={form.birthdate}
                    onChange={(e) => setForm({ ...form, birthdate: e.target.value })}
                  />
                </label>
                <label className="block space-y-1">
                  <span className="text-sm text-sand/60">
                    Unidade <span className="text-sand/40">(onde já é aluno confirmado)</span>
                  </span>
                  <input
                    className="w-full rounded-md border border-white/15 bg-ink px-3 py-2"
                    placeholder="preenchido automaticamente pela IA"
                    value={form.unit}
                    onChange={(e) => setForm({ ...form, unit: e.target.value })}
                  />
                </label>
                <label className="block space-y-1">
                  <span className="text-sm text-sand/60">Estágio</span>
                  <input
                    className="w-full rounded-md border border-white/15 bg-ink px-3 py-2"
                    placeholder="ex: novo, qualificado, transferido, matriculado"
                    value={form.stage}
                    onChange={(e) => setForm({ ...form, stage: e.target.value })}
                  />
                </label>
                <label className="block space-y-1">
                  <span className="text-sm text-sand/60">
                    Funil de vendas <span className="text-sand/40">(a IA avança sozinha; matriculado é manual)</span>
                  </span>
                  <select
                    className="w-full rounded-md border border-white/15 bg-ink px-3 py-2"
                    value={form.sales_stage}
                    onChange={(e) => setForm({ ...form, sales_stage: e.target.value })}
                  >
                    <option value="">Fora do funil</option>
                    {Object.entries(SALES_STAGE_LABELS).map(([value, label]) => (
                      <option key={value} value={value}>
                        {label}
                      </option>
                    ))}
                  </select>
                  {selected.sales_stage === "perdido" && selected.lost_reason && (
                    <span className="block text-xs text-sand/45">
                      Motivo: {LOST_REASON_LABELS[selected.lost_reason] || selected.lost_reason}
                    </span>
                  )}
                </label>
              </div>

              {Object.keys(selected.custom_fields || {}).length > 0 && (
                <div>
                  <p className="text-sm text-sand/60">Outros dados</p>
                  <pre className="mt-1 overflow-auto rounded-md border border-white/10 bg-ink px-3 py-2 text-xs text-sand/70">
                    {JSON.stringify(selected.custom_fields, null, 2)}
                  </pre>
                </div>
              )}

              {msg && <p className="text-lime">{msg}</p>}

              <button type="submit" className="rounded-md bg-leaf px-5 py-2.5 font-semibold text-white hover:bg-lime">
                Salvar
              </button>
            </form>
          )}
        </div>
      </div>
    </div>
  );
}
