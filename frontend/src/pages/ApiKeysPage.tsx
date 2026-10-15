import { FormEvent, useEffect, useState } from "react";
import { api } from "../api";

type ApiKey = {
  id: string;
  name: string;
  key_prefix: string;
  is_active: boolean;
  can_write_sales?: boolean;
  last_used_at: string | null;
  created_at: string;
};

export default function ApiKeysPage() {
  const [items, setItems] = useState<ApiKey[]>([]);
  const [name, setName] = useState("");
  const [canWriteSales, setCanWriteSales] = useState(false);
  const [newKey, setNewKey] = useState<string | null>(null);
  const [error, setError] = useState("");

  const load = () => {
    api<ApiKey[]>("/api-keys")
      .then(setItems)
      .catch((e) => setError(e.message));
  };

  useEffect(() => {
    load();
  }, []);

  const onCreate = async (e: FormEvent) => {
    e.preventDefault();
    if (!name.trim()) return;
    setError("");
    try {
      const created = await api<ApiKey & { key: string }>("/api-keys", {
        method: "POST",
        body: JSON.stringify({ name: name.trim(), can_write_sales: canWriteSales }),
      });
      setNewKey(created.key);
      setName("");
      setCanWriteSales(false);
      await load();
    } catch (err) {
      setError(err instanceof Error ? err.message : "Erro ao criar chave");
    }
  };

  const onRevoke = async (key: ApiKey) => {
    if (!confirm(`Revogar a chave "${key.name}"? Sistemas usando ela param de funcionar imediatamente.`))
      return;
    setError("");
    try {
      await api(`/api-keys/${key.id}`, { method: "DELETE" });
      await load();
    } catch (err) {
      setError(err instanceof Error ? err.message : "Erro ao revogar");
    }
  };

  return (
    <div className="space-y-8">
      <div>
        <h2 className="font-display text-3xl font-bold">API externa</h2>
        <p className="mt-1 text-sand/55">
          Chaves de acesso pra outros sistemas (n8n, BI, planilhas) consultarem clientes e métricas
          só-leitura, sem precisar de login de usuário.
        </p>
      </div>

      {error && <p className="text-ember">{error}</p>}

      {newKey && (
        <div className="space-y-2 border border-leaf/40 bg-leaf/10 p-4">
          <p className="font-semibold text-lime">
            Chave criada! Copia agora — ela não vai aparecer de novo depois que você sair dessa tela.
          </p>
          <div className="flex items-center gap-2">
            <code className="flex-1 overflow-x-auto rounded-md border border-white/15 bg-ink px-3 py-2 text-sm">
              {newKey}
            </code>
            <button
              type="button"
              onClick={() => navigator.clipboard.writeText(newKey)}
              className="shrink-0 rounded-md border border-leaf/40 px-3 py-2 text-sm text-lime hover:bg-leaf/10"
            >
              Copiar
            </button>
          </div>
          <p className="pt-2 text-sm text-sand/60">
            Ou manda direto esse link — quem abrir só vê as métricas, sem nenhum acesso de admin:
          </p>
          <div className="flex items-center gap-2">
            <code className="flex-1 overflow-x-auto rounded-md border border-white/15 bg-ink px-3 py-2 text-sm">
              {`${window.location.origin}/metrics-view?key=${newKey}`}
            </code>
            <button
              type="button"
              onClick={() => navigator.clipboard.writeText(`${window.location.origin}/metrics-view?key=${newKey}`)}
              className="shrink-0 rounded-md border border-leaf/40 px-3 py-2 text-sm text-lime hover:bg-leaf/10"
            >
              Copiar link
            </button>
          </div>
          <button
            type="button"
            onClick={() => setNewKey(null)}
            className="text-sm text-sand/50 hover:text-sand"
          >
            Já copiei, fechar
          </button>
        </div>
      )}

      <form onSubmit={onCreate} className="space-y-3 border border-white/10 bg-panel p-5">
        <div className="flex gap-3">
          <input
            className="flex-1 rounded-md border border-white/15 bg-ink px-3 py-2"
            placeholder="Nome (ex: n8n - relatório mensal)"
            value={name}
            onChange={(e) => setName(e.target.value)}
            required
          />
          <button type="submit" className="rounded-md bg-leaf px-5 py-2.5 font-semibold text-white hover:bg-lime">
            Criar chave
          </button>
        </div>
        <label className="flex items-start gap-2 text-sm text-sand/70">
          <input
            type="checkbox"
            className="mt-1"
            checked={canWriteSales}
            onChange={(e) => setCanWriteSales(e.target.checked)}
          />
          <span>
            Pode <strong>enviar vendas</strong> (para o fluxo do n8n que alimenta o ranking de vendas). Deixe
            desmarcado nas chaves só de leitura, como a do link de métricas.
          </span>
        </label>
      </form>

      <ul className="divide-y divide-white/10 border border-white/10">
        {items.map((k) => (
          <li key={k.id} className="flex items-center justify-between gap-3 px-4 py-3">
            <div>
              <p className="font-medium">
                {k.name} <span className="text-sm text-sand/50">({k.key_prefix}…)</span>
                {k.can_write_sales && (
                  <span className="ml-2 rounded border border-leaf/40 px-1.5 py-0.5 text-xs text-lime">envia vendas</span>
                )}
              </p>
              <p className="text-xs text-sand/40">
                Criada em {new Date(k.created_at).toLocaleDateString("pt-BR")}
                {k.last_used_at
                  ? ` · último uso em ${new Date(k.last_used_at).toLocaleString("pt-BR")}`
                  : " · nunca usada"}
              </p>
            </div>
            <button
              type="button"
              onClick={() => onRevoke(k)}
              className="shrink-0 rounded-md border border-ember/40 px-3 py-1.5 text-sm text-ember hover:bg-ember/10"
            >
              Revogar
            </button>
          </li>
        ))}
        {items.length === 0 && (
          <li className="px-4 py-8 text-center text-sand/45">Nenhuma chave criada ainda.</li>
        )}
      </ul>

      <section className="space-y-3 border border-white/10 bg-panel p-5">
        <h3 className="font-display text-lg font-semibold">Como usar</h3>
        <p className="text-sm text-sand/60">
          Manda a chave no header <code className="text-lime">Authorization: Bearer SUA_CHAVE</code>.
          Endpoints disponíveis:
        </p>
        <ul className="space-y-1 text-sm text-sand/60">
          <li>
            <code className="text-lime">GET /api/v1/leads</code> — lista clientes (filtros:{" "}
            <code>stage</code>, <code>updated_since</code>)
          </li>
          <li>
            <code className="text-lime">GET /api/v1/metrics/overview</code> — visão geral
          </li>
          <li>
            <code className="text-lime">GET /api/v1/metrics/leads-funnel</code> — funil por estágio
          </li>
          <li>
            <code className="text-lime">GET /api/v1/metrics/tools</code> — uso por ferramenta
          </li>
          <li>
            <code className="text-lime">GET /api/v1/metrics/featured-tools</code> — ferramentas
            destacadas
          </li>
          <li>
            <code className="text-lime">GET /api/v1/sales/contracts</code> — contratos de venda nova
            guardados (filtros: <code>date_from</code>, <code>date_to</code>, <code>unidade</code>,{" "}
            <code>responsavel</code>)
          </li>
          <li>
            <code className="text-lime">POST /api/v1/sales/contracts</code> — envia contratos de uma
            unidade (só chave com <em>enviar vendas</em>)
          </li>
        </ul>

        <div className="space-y-2 pt-2">
          <p className="text-sm text-sand/60">Exemplo de requisição de verdade (com curl):</p>
          <pre className="overflow-x-auto rounded-md border border-white/10 bg-ink px-3 py-2 text-xs text-sand/80">
            {`curl -H "Authorization: Bearer SUA_CHAVE" \\
  "${window.location.origin}/api/v1/leads?updated_since=2026-01-01T00:00:00Z&limit=50"`}
          </pre>
          <p className="text-xs text-sand/40">
            Resposta é uma lista de objetos JSON, um por cliente — mesmos campos da tela Clientes
            (phone, name, cpf, email, unit, stage, is_student, was_transferred,
            wants_cancellation...). Sem <code>updated_since</code>, retorna os mais recentes primeiro.
          </p>
        </div>
      </section>
    </div>
  );
}
