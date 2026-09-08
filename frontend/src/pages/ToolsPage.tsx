import { FormEvent, useEffect, useState } from "react";
import { api } from "../api";
import { useAuth } from "../auth";

type ToolParameter = {
  name: string;
  type: string;
  description: string;
  required: boolean;
};

type Tool = {
  id: string;
  name: string;
  tool_key: string;
  description: string | null;
  parameters: ToolParameter[];
  webhook_url: string | null;
  integration_id: string | null;
  is_active: boolean;
  featured_in_metrics: boolean;
  last_executed_at: string | null;
};

type Integration = {
  id: string;
  name: string;
};

const PRESETS: { key: string; label: string; description: string }[] = [
  {
    key: "transferir_atendimento",
    label: "Transferir atendimento",
    description: "Transfere a conversa para um atendente humano quando o cliente pedir ou precisar.",
  },
  {
    key: "encerrar_atendimento",
    label: "Encerrar atendimento",
    description: "Encerra o atendimento quando a conversa já foi resolvida.",
  },
  {
    key: "enviar_imagens_planos",
    label: "Enviar imagens dos planos",
    description: "Envia para o cliente as imagens/tabela de preços dos planos da unidade.",
  },
  {
    key: "consultar_aluno",
    label: "Consultar aluno",
    description: "Consulta os dados do aluno pelo CPF para ajudar no atendimento.",
  },
  { key: "", label: "Personalizada", description: "" },
];

const emptyParam = (): ToolParameter => ({ name: "", type: "string", description: "", required: false });

export default function ToolsPage() {
  const { companyId } = useAuth();
  const [items, setItems] = useState<Tool[]>([]);
  const [error, setError] = useState("");

  const [presetKey, setPresetKey] = useState(PRESETS[0].key);
  const [customKey, setCustomKey] = useState("");
  const [name, setName] = useState(PRESETS[0].label);
  const [description, setDescription] = useState(PRESETS[0].description);
  const [webhookUrl, setWebhookUrl] = useState("");
  const [params, setParams] = useState<ToolParameter[]>([]);
  const [editingId, setEditingId] = useState<string | null>(null);
  const [integrations, setIntegrations] = useState<Integration[]>([]);
  const [integrationId, setIntegrationId] = useState("");
  const [featuredInMetrics, setFeaturedInMetrics] = useState(false);

  const load = () => {
    if (!companyId) return;
    api<Tool[]>(`/ai-configs/${companyId}/tools`)
      .then(setItems)
      .catch((e) => setError(e.message));
  };

  useEffect(() => {
    load();
    api<Integration[]>("/integrations").then(setIntegrations).catch(() => {});
  }, [companyId]);

  const onPresetChange = (key: string) => {
    setPresetKey(key);
    const preset = PRESETS.find((p) => p.key === key);
    if (preset && preset.key) {
      setName(preset.label);
      setDescription(preset.description);
    } else {
      setName("");
      setDescription("");
    }
  };

  const onAddParam = () => setParams((prev) => [...prev, emptyParam()]);
  const onRemoveParam = (i: number) => setParams((prev) => prev.filter((_, idx) => idx !== i));
  const onParamChange = (i: number, patch: Partial<ToolParameter>) =>
    setParams((prev) => prev.map((p, idx) => (idx === i ? { ...p, ...patch } : p)));

  const resetForm = () => {
    setEditingId(null);
    setName(PRESETS[0].label);
    setDescription(PRESETS[0].description);
    setPresetKey(PRESETS[0].key);
    setCustomKey("");
    setWebhookUrl("");
    setParams([]);
    setIntegrationId("");
    setFeaturedInMetrics(false);
  };

  const onEdit = (tool: Tool) => {
    setEditingId(tool.id);
    const preset = PRESETS.find((p) => p.key === tool.tool_key);
    setPresetKey(preset ? preset.key : "");
    setCustomKey(preset ? "" : tool.tool_key);
    setName(tool.name);
    setDescription(tool.description ?? "");
    setWebhookUrl(tool.webhook_url ?? "");
    setParams(tool.parameters.map((p) => ({ ...p })));
    setIntegrationId(tool.integration_id ?? "");
    setFeaturedInMetrics(tool.featured_in_metrics);
    window.scrollTo({ top: 0, behavior: "smooth" });
  };

  const onSubmit = async (e: FormEvent) => {
    e.preventDefault();
    if (!companyId) return;
    setError("");

    if (editingId) {
      try {
        await api(`/ai-configs/${companyId}/tools/${editingId}`, {
          method: "PATCH",
          body: JSON.stringify({
            name,
            description,
            webhook_url: webhookUrl,
            parameters: params.filter((p) => p.name.trim()),
            integration_id: integrationId || null,
            featured_in_metrics: featuredInMetrics,
          }),
        });
        resetForm();
        await load();
      } catch (err) {
        setError(err instanceof Error ? err.message : "Erro ao salvar ferramenta");
      }
      return;
    }

    const toolKey = presetKey || customKey.trim();
    if (!toolKey) {
      setError("Informe uma chave para a ferramenta personalizada (ex: consultar_pagamento).");
      return;
    }
    try {
      await api(`/ai-configs/${companyId}/tools`, {
        method: "POST",
        body: JSON.stringify({
          name,
          tool_key: toolKey,
          description,
          webhook_url: webhookUrl,
          parameters: params.filter((p) => p.name.trim()),
          integration_id: integrationId || null,
          featured_in_metrics: featuredInMetrics,
        }),
      });
      resetForm();
      await load();
    } catch (err) {
      setError(err instanceof Error ? err.message : "Erro ao criar ferramenta");
    }
  };

  const onToggleActive = async (tool: Tool) => {
    setError("");
    try {
      await api(`/ai-configs/${companyId}/tools/${tool.id}`, {
        method: "PATCH",
        body: JSON.stringify({ is_active: !tool.is_active }),
      });
      await load();
    } catch (err) {
      setError(err instanceof Error ? err.message : "Erro ao atualizar");
    }
  };

  const onDelete = async (tool: Tool) => {
    if (!confirm(`Excluir a ferramenta "${tool.name}"?`)) return;
    setError("");
    try {
      await api(`/ai-configs/${companyId}/tools/${tool.id}`, { method: "DELETE" });
      await load();
    } catch (err) {
      setError(err instanceof Error ? err.message : "Erro ao excluir");
    }
  };

  if (!companyId) {
    return <p className="text-sand/60">Selecione uma empresa.</p>;
  }

  const globalTools = items.filter((t) => !t.integration_id);
  const groups = [
    { key: "global", label: "Todas (global)", tools: globalTools },
    ...integrations.map((i) => ({
      key: i.id,
      label: i.name,
      tools: items.filter((t) => t.integration_id === i.id),
    })),
  ];

  return (
    <div className="space-y-8">
      <div>
        <h2 className="font-display text-3xl font-bold">Ferramentas</h2>
        <p className="mt-1 text-sand/55">
          Ações que a IA pode executar via webhook (n8n): transferir atendimento, enviar imagens,
          consultar dados etc. O webhook recebe os argumentos que a IA extrai da conversa e o
          telefone do cliente, e responde se deu certo.
        </p>
      </div>

      <form onSubmit={onSubmit} className="space-y-4 border border-white/10 bg-panel p-5">
        <div className="grid gap-3 sm:grid-cols-2">
          <label className="block space-y-1">
            <span className="text-sm text-sand/60">Tipo</span>
            <select
              className="w-full rounded-md border border-white/15 bg-ink px-3 py-2 disabled:opacity-50"
              value={presetKey}
              onChange={(e) => onPresetChange(e.target.value)}
              disabled={!!editingId}
            >
              {PRESETS.map((p) => (
                <option key={p.label} value={p.key}>
                  {p.label}
                </option>
              ))}
            </select>
          </label>
          {!presetKey && (
            <label className="block space-y-1">
              <span className="text-sm text-sand/60">Chave (tool_key)</span>
              <input
                className="w-full rounded-md border border-white/15 bg-ink px-3 py-2 disabled:opacity-50"
                placeholder="ex: consultar_pagamento"
                value={customKey}
                onChange={(e) => setCustomKey(e.target.value.trim())}
                required={!presetKey}
                disabled={!!editingId}
              />
            </label>
          )}
        </div>
        {editingId && (
          <p className="text-xs text-sand/40">
            Editando "{name}" — a chave (tool_key) não pode ser alterada depois de criada.
          </p>
        )}

        <label className="block space-y-1">
          <span className="text-sm text-sand/60">Nome</span>
          <input
            className="w-full rounded-md border border-white/15 bg-ink px-3 py-2"
            value={name}
            onChange={(e) => setName(e.target.value)}
            required
          />
        </label>

        <label className="block space-y-1">
          <span className="text-sm text-sand/60">
            Descrição <span className="text-sand/40">(a IA usa isso pra saber quando chamar)</span>
          </span>
          <textarea
            className="min-h-20 w-full rounded-md border border-white/15 bg-ink px-3 py-2"
            value={description}
            onChange={(e) => setDescription(e.target.value)}
          />
        </label>

        <label className="block space-y-1">
          <span className="text-sm text-sand/60">
            Integração <span className="text-sand/40">(em qual conversa a IA usa esse webhook)</span>
          </span>
          <select
            className="w-full rounded-md border border-white/15 bg-ink px-3 py-2"
            value={integrationId}
            onChange={(e) => setIntegrationId(e.target.value)}
          >
            <option value="">Todas (global)</option>
            {integrations.map((i) => (
              <option key={i.id} value={i.id}>
                {i.name}
              </option>
            ))}
          </select>
        </label>

        <label className="flex items-center gap-2">
          <input
            type="checkbox"
            checked={featuredInMetrics}
            onChange={(e) => setFeaturedInMetrics(e.target.checked)}
          />
          <span className="text-sm text-sand/60">
            Destacar essa ferramenta no Painel{" "}
            <span className="text-sand/40">(vira um card com o total de chamadas com sucesso)</span>
          </span>
        </label>

        <label className="block space-y-1">
          <span className="text-sm text-sand/60">URL do webhook (n8n)</span>
          <input
            className="w-full rounded-md border border-white/15 bg-ink px-3 py-2"
            placeholder="https://n8n2.mov.pro.br/webhook/..."
            value={webhookUrl}
            onChange={(e) => setWebhookUrl(e.target.value)}
            required
          />
        </label>

        <div className="space-y-2">
          <div className="flex items-center justify-between">
            <span className="text-sm text-sand/60">
              Parâmetros <span className="text-sand/40">(o que a IA deve extrair da conversa)</span>
            </span>
            <button
              type="button"
              onClick={onAddParam}
              className="rounded-md border border-leaf px-3 py-1 text-sm text-lime hover:bg-leaf/10"
            >
              + Parâmetro
            </button>
          </div>
          {params.map((p, i) => (
            <div key={i} className="grid gap-2 border border-white/10 p-3 sm:grid-cols-[1.2fr_0.8fr_1.5fr_auto_auto]">
              <input
                className="rounded-md border border-white/15 bg-ink px-2 py-1.5 text-sm"
                placeholder="nome (ex: cpf)"
                value={p.name}
                onChange={(e) => onParamChange(i, { name: e.target.value })}
              />
              <select
                className="rounded-md border border-white/15 bg-ink px-2 py-1.5 text-sm"
                value={p.type}
                onChange={(e) => onParamChange(i, { type: e.target.value })}
              >
                <option value="string">texto</option>
                <option value="number">número</option>
                <option value="boolean">sim/não</option>
              </select>
              <input
                className="rounded-md border border-white/15 bg-ink px-2 py-1.5 text-sm"
                placeholder="descrição pra IA"
                value={p.description}
                onChange={(e) => onParamChange(i, { description: e.target.value })}
              />
              <label className="flex items-center gap-1 text-xs text-sand/60">
                <input
                  type="checkbox"
                  checked={p.required}
                  onChange={(e) => onParamChange(i, { required: e.target.checked })}
                />
                obrigatório
              </label>
              <button
                type="button"
                onClick={() => onRemoveParam(i)}
                className="rounded-md border border-ember/40 px-2 text-xs text-ember hover:bg-ember/10"
              >
                remover
              </button>
            </div>
          ))}
        </div>

        {error && <p className="text-ember">{error}</p>}

        <div className="flex items-center gap-3">
          <button type="submit" className="rounded-md bg-leaf px-5 py-2.5 font-semibold text-white hover:bg-lime">
            {editingId ? "Salvar alterações" : "Criar ferramenta"}
          </button>
          {editingId && (
            <button
              type="button"
              onClick={resetForm}
              className="rounded-md border border-white/15 px-5 py-2.5 font-semibold text-sand/60 hover:bg-white/5"
            >
              Cancelar
            </button>
          )}
        </div>
      </form>

      <div className="space-y-6">
        {groups.map((g) => (
          <section key={g.key} className="space-y-2">
            <h3 className="font-display text-sm font-semibold uppercase tracking-wide text-sand/50">
              {g.label} <span className="text-sand/30">({g.tools.length})</span>
            </h3>
            <ul className="divide-y divide-white/10 border border-white/10">
              {g.tools.map((t) => (
                <li key={t.id} className="space-y-2 px-4 py-3">
                  <div className="flex items-start justify-between gap-3">
                    <div>
                      <p className="font-medium">
                        {t.name} <span className="text-sm text-sand/50">({t.tool_key})</span>
                        {t.featured_in_metrics && (
                          <span className="ml-2 rounded border border-leaf/40 px-1.5 py-0.5 text-xs text-lime">
                            destacada no Painel
                          </span>
                        )}
                      </p>
                      {t.description && <p className="mt-0.5 text-sm text-sand/60">{t.description}</p>}
                      {t.webhook_url && <p className="mt-1 break-all text-xs text-sand/40">{t.webhook_url}</p>}
                      {t.parameters.length > 0 && (
                        <p className="mt-1 text-xs text-sand/40">
                          Parâmetros: {t.parameters.map((p) => p.name).join(", ")}
                        </p>
                      )}
                    </div>
                    <div className="flex shrink-0 items-center gap-2">
                      <button
                        type="button"
                        onClick={() => onEdit(t)}
                        className="rounded-md border border-white/15 px-2 py-1 text-xs text-sand/60 hover:bg-white/5"
                      >
                        Editar
                      </button>
                      <button
                        type="button"
                        onClick={() => onToggleActive(t)}
                        className={`rounded-md border px-2 py-1 text-xs ${
                          t.is_active
                            ? "border-leaf/40 text-lime hover:bg-leaf/10"
                            : "border-white/15 text-sand/50 hover:bg-white/5"
                        }`}
                      >
                        {t.is_active ? "Ativa" : "Inativa"}
                      </button>
                      <button
                        type="button"
                        onClick={() => onDelete(t)}
                        className="rounded-md border border-ember/40 px-2 py-1 text-xs text-ember hover:bg-ember/10"
                      >
                        Excluir
                      </button>
                    </div>
                  </div>
                </li>
              ))}
              {g.tools.length === 0 && (
                <li className="px-4 py-6 text-center text-sand/45">Nenhuma ferramenta ainda.</li>
              )}
            </ul>
          </section>
        ))}
      </div>
    </div>
  );
}
