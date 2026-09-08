import { FormEvent, useEffect, useState } from "react";
import { api } from "../api";

type ApiKey = {
  id: string;
  name: string;
  key_prefix: string;
  is_active: boolean;
  last_used_at: string | null;
  created_at: string;
};

export default function ApiKeysPage() {
  const [items, setItems] = useState<ApiKey[]>([]);
  const [name, setName] = useState("");
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
        body: JSON.stringify({ name: name.trim() }),
      });
      setNewKey(created.key);
      setName("");
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
          <button
            type="button"
            onClick={() => setNewKey(null)}
            className="text-sm text-sand/50 hover:text-sand"
          >
            Já copiei, fechar
          </button>
        </div>
      )}

      <form onSubmit={onCreate} className="flex gap-3 border border-white/10 bg-panel p-5">
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
      </form>

      <ul className="divide-y divide-white/10 border border-white/10">
        {items.map((k) => (
          <li key={k.id} className="flex items-center justify-between gap-3 px-4 py-3">
            <div>
              <p className="font-medium">
                {k.name} <span className="text-sm text-sand/50">({k.key_prefix}…)</span>
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
        </ul>
      </section>
    </div>
  );
}
