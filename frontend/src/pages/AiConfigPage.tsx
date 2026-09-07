import { FormEvent, useEffect, useState } from "react";
import { api } from "../api";
import { useAuth } from "../auth";

type AiConfig = {
  id: string;
  company_id: string;
  integration_id: string | null;
  ai_name: string;
  tone: string | null;
  use_emoji: boolean;
  system_prompt: string;
  llm_provider: string;
  llm_model: string;
  llm_api_key_masked: string | null;
  has_api_key: boolean;
  temperature: number;
  operation_mode: string;
};

type Integration = {
  id: string;
  name: string;
};

type Rag = {
  id: string;
  name: string;
  source_type: string;
  webhook_url: string;
  is_active: boolean;
};

const OPENAI_MODELS = ["gpt-4o-mini", "gpt-4o", "gpt-4.1-mini", "gpt-4.1", "o4-mini"];

export default function AiConfigPage() {
  const { companyId } = useAuth();
  const [integrations, setIntegrations] = useState<Integration[]>([]);
  const [integrationId, setIntegrationId] = useState("");
  const [config, setConfig] = useState<AiConfig | null>(null);
  const [rags, setRags] = useState<Rag[]>([]);
  const [apiKey, setApiKey] = useState("");
  const [ragName, setRagName] = useState("");
  const [ragUrl, setRagUrl] = useState("");
  const [msg, setMsg] = useState("");
  const [error, setError] = useState("");

  const loadConfig = async (targetIntegrationId: string) => {
    if (!companyId) return;
    const qs = targetIntegrationId ? `?integration_id=${targetIntegrationId}` : "";
    const cfg = await api<AiConfig>(`/ai-configs/${companyId}${qs}`);
    setConfig(cfg);
    setApiKey("");
  };

  useEffect(() => {
    if (!companyId) return;
    api<Integration[]>("/integrations").then(setIntegrations).catch(() => {});
    api<Rag[]>(`/ai-configs/${companyId}/rag-sources`).then(setRags).catch((e) => setError(e.message));
  }, [companyId]);

  useEffect(() => {
    loadConfig(integrationId).catch((e) => setError(e.message));
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [companyId, integrationId]);

  const save = async (e: FormEvent) => {
    e.preventDefault();
    if (!companyId || !config) return;
    setError("");
    setMsg("");
    try {
      const qs = integrationId ? `?integration_id=${integrationId}` : "";
      const body: Record<string, unknown> = {
        ai_name: config.ai_name,
        tone: config.tone,
        use_emoji: config.use_emoji,
        system_prompt: config.system_prompt,
        llm_provider: config.llm_provider,
        llm_model: config.llm_model,
        temperature: config.temperature,
        operation_mode: config.operation_mode,
      };
      if (apiKey.trim()) body.llm_api_key = apiKey.trim();
      const updated = await api<AiConfig>(`/ai-configs/${companyId}${qs}`, {
        method: "PATCH",
        body: JSON.stringify(body),
      });
      setConfig(updated);
      setApiKey("");
      setMsg("Configuração salva.");
    } catch (err) {
      setError(err instanceof Error ? err.message : "Erro ao salvar");
    }
  };

  const loadRags = async () => {
    if (!companyId) return;
    const list = await api<Rag[]>(`/ai-configs/${companyId}/rag-sources`);
    setRags(list);
  };

  const addRag = async (e: FormEvent) => {
    e.preventDefault();
    if (!companyId) return;
    await api(`/ai-configs/${companyId}/rag-sources`, {
      method: "POST",
      body: JSON.stringify({ name: ragName, webhook_url: ragUrl }),
    });
    setRagName("");
    setRagUrl("");
    await loadRags();
  };

  const toggleRagActive = async (rag: Rag) => {
    if (!companyId) return;
    await api(`/ai-configs/${companyId}/rag-sources/${rag.id}`, {
      method: "PATCH",
      body: JSON.stringify({ is_active: !rag.is_active }),
    });
    await loadRags();
  };

  const deleteRag = async (rag: Rag) => {
    if (!companyId) return;
    if (!confirm(`Excluir a RAG "${rag.name}"?`)) return;
    await api(`/ai-configs/${companyId}/rag-sources/${rag.id}`, { method: "DELETE" });
    await loadRags();
  };

  if (!companyId) {
    return <p className="text-sand/60">Selecione uma empresa.</p>;
  }

  if (!config) {
    return <p className="text-sand/60">Carregando configuração…</p>;
  }

  type Warning = { level: "error" | "warning"; text: string };
  const warnings: Warning[] = [];
  if (!config.has_api_key) {
    warnings.push({
      level: "error",
      text: "Sem API key da OpenAI configurada — a IA não vai gerar nenhuma resposta até isso ser preenchido.",
    });
  }
  if (config.operation_mode === "off") {
    warnings.push({
      level: "error",
      text: "Modo de operação está \"Desligado\" — a IA não vai responder automaticamente a nenhuma mensagem.",
    });
  }
  if (config.operation_mode === "suggest") {
    warnings.push({
      level: "warning",
      text: 'Modo "Só sugerir" está ativo — a IA não envia a resposta direto ao cliente, só deixa sugerida para aprovação humana.',
    });
  }
  const activeRags = rags.filter((r) => r.is_active);
  if (activeRags.length === 0 && rags.length > 0) {
    warnings.push({
      level: "warning",
      text: "Todas as RAGs cadastradas estão inativas — a IA não vai consultar nenhuma base externa.",
    });
  }

  return (
    <div className="space-y-8">
      <div>
        <h2 className="font-display text-3xl font-bold">Configuração da IA</h2>
        <p className="mt-1 text-sand/55">
          Nome, personalidade, provedor LLM, API key e RAGs via webhook n8n.
        </p>
      </div>

      <label className="block max-w-sm space-y-1">
        <span className="text-sm text-sand/60">
          Personalizar para integração{" "}
          <span className="text-sand/40">
            (opcional — nome, tom de voz e chave de API podem ser diferentes por integração;
            sem selecionar, edita a configuração padrão da empresa)
          </span>
        </span>
        <select
          className="w-full rounded-md border border-white/15 bg-ink px-3 py-2"
          value={integrationId}
          onChange={(e) => setIntegrationId(e.target.value)}
        >
          <option value="">Padrão da empresa</option>
          {integrations.map((i) => (
            <option key={i.id} value={i.id}>
              {i.name}
            </option>
          ))}
        </select>
      </label>

      {warnings.length > 0 && (
        <ul className="space-y-2">
          {warnings.map((w, i) => (
            <li
              key={i}
              className={`rounded-md border px-4 py-2.5 text-sm ${
                w.level === "error"
                  ? "border-ember/40 bg-ember/10 text-ember"
                  : "border-amber-400/40 bg-amber-400/10 text-amber-300"
              }`}
            >
              {w.level === "error" ? "⚠ " : "• "}
              {w.text}
            </li>
          ))}
        </ul>
      )}

      <form onSubmit={save} className="space-y-4 border border-white/10 bg-panel p-5">
        <label className="block space-y-1 sm:max-w-xs">
          <span className="text-sm text-sand/60">Nome da IA</span>
          <input
            className="w-full rounded-md border border-white/15 bg-ink px-3 py-2"
            placeholder="Ex: Mônica"
            value={config.ai_name}
            onChange={(e) => setConfig({ ...config, ai_name: e.target.value })}
            required
          />
        </label>

        <label className="block space-y-1">
          <span className="text-sm text-sand/60">
            Tom de voz <span className="text-sand/40">(descrição curta da personalidade)</span>
          </span>
          <textarea
            className="min-h-20 w-full rounded-md border border-white/15 bg-ink px-3 py-2"
            placeholder="Ex: Caloroso, simpático e acolhedor — fala como alguém que gosta de ajudar, sem soar robótica ou formal demais."
            value={config.tone ?? ""}
            onChange={(e) => setConfig({ ...config, tone: e.target.value })}
          />
        </label>

        <label className="flex items-center gap-2">
          <input
            type="checkbox"
            checked={config.use_emoji}
            onChange={(e) => setConfig({ ...config, use_emoji: e.target.checked })}
          />
          <span className="text-sm text-sand/60">Pode usar emoji nas respostas</span>
        </label>

        <div className="grid gap-3 sm:grid-cols-3">
          <label className="block space-y-1">
            <span className="text-sm text-sand/60">Provedor</span>
            <select
              className="w-full rounded-md border border-white/15 bg-ink px-3 py-2"
              value={config.llm_provider}
              onChange={(e) => setConfig({ ...config, llm_provider: e.target.value })}
            >
              <option value="openai">OpenAI</option>
              <option value="anthropic" disabled>
                Anthropic (em breve)
              </option>
            </select>
          </label>
          <label className="block space-y-1">
            <span className="text-sm text-sand/60">Modelo</span>
            <select
              className="w-full rounded-md border border-white/15 bg-ink px-3 py-2"
              value={config.llm_model}
              onChange={(e) => setConfig({ ...config, llm_model: e.target.value })}
            >
              {OPENAI_MODELS.map((m) => (
                <option key={m} value={m}>
                  {m}
                </option>
              ))}
            </select>
          </label>
          <label className="block space-y-1">
            <span className="text-sm text-sand/60">Temperatura</span>
            <input
              type="number"
              step="0.1"
              min="0"
              max="2"
              className="w-full rounded-md border border-white/15 bg-ink px-3 py-2"
              value={config.temperature}
              onChange={(e) =>
                setConfig({ ...config, temperature: Number(e.target.value) })
              }
            />
          </label>
        </div>

        <label className="block space-y-1">
          <span className="text-sm text-sand/60">
            API Key OpenAI{" "}
            {config.has_api_key && (
              <span className="text-lime">
                (atual: {config.llm_api_key_masked})
              </span>
            )}
          </span>
          <input
            type="password"
            className="w-full rounded-md border border-white/15 bg-ink px-3 py-2"
            placeholder={config.has_api_key ? "•••• cole nova key para rotacionar" : "sk-..."}
            value={apiKey}
            onChange={(e) => setApiKey(e.target.value)}
          />
        </label>

        <label className="block space-y-1">
          <span className="text-sm text-sand/60">Modo de operação</span>
          <select
            className="w-full rounded-md border border-white/15 bg-ink px-3 py-2 sm:max-w-xs"
            value={config.operation_mode}
            onChange={(e) => setConfig({ ...config, operation_mode: e.target.value })}
          >
            <option value="auto">Automático</option>
            <option value="suggest">Só sugerir</option>
            <option value="off">Desligado</option>
          </select>
        </label>

        <details className="rounded-md border border-white/10 p-3">
          <summary className="cursor-pointer text-sm text-sand/60">
            Instruções extras (avançado, opcional)
          </summary>
          <label className="mt-3 block space-y-1">
            <span className="text-xs text-sand/40">
              Regra específica que não caiba em nome/tom/emoji — some ao prompt montado a partir
              dos campos acima. Use <code className="text-lime">{"{ai_name}"}</code> se quiser
              citar o nome da IA aqui.
            </span>
            <textarea
              className="min-h-24 w-full rounded-md border border-white/15 bg-ink px-3 py-2"
              value={config.system_prompt}
              onChange={(e) => setConfig({ ...config, system_prompt: e.target.value })}
            />
          </label>
        </details>

        {msg && <p className="text-lime">{msg}</p>}
        {error && <p className="text-ember">{error}</p>}

        <button type="submit" className="rounded-md bg-leaf px-5 py-2.5 font-semibold text-white hover:bg-lime">
          Salvar
        </button>
      </form>

      <section className="space-y-4">
        <h3 className="font-display text-xl font-semibold">RAGs (n8n)</h3>
        <p className="text-sm text-sand/45">
          RAGs valem pra empresa toda, independente da integração selecionada acima.
        </p>
        <form onSubmit={addRag} className="grid gap-3 sm:grid-cols-3">
          <input
            className="rounded-md border border-white/15 bg-ink px-3 py-2"
            placeholder="Nome (ex: Planos)"
            value={ragName}
            onChange={(e) => setRagName(e.target.value)}
            required
          />
          <input
            className="rounded-md border border-white/15 bg-ink px-3 py-2 sm:col-span-1"
            placeholder="URL webhook n8n"
            value={ragUrl}
            onChange={(e) => setRagUrl(e.target.value)}
            required
          />
          <button type="submit" className="rounded-md border border-leaf px-4 py-2 text-lime hover:bg-leaf/10">
            Adicionar RAG
          </button>
        </form>
        <ul className="divide-y divide-white/10 border border-white/10">
          {rags.map((r) => (
            <li key={r.id} className="flex items-start justify-between gap-3 px-4 py-3">
              <div>
                <p className="font-medium">{r.name}</p>
                <p className="truncate text-sm text-sand/50">{r.webhook_url}</p>
              </div>
              <div className="flex shrink-0 items-center gap-2">
                <button
                  type="button"
                  onClick={() => toggleRagActive(r)}
                  className={`rounded-md border px-2 py-1 text-xs ${
                    r.is_active
                      ? "border-leaf/40 text-lime hover:bg-leaf/10"
                      : "border-white/15 text-sand/50 hover:bg-white/5"
                  }`}
                >
                  {r.is_active ? "Ativa" : "Inativa"}
                </button>
                <button
                  type="button"
                  onClick={() => deleteRag(r)}
                  className="rounded-md border border-ember/40 px-2 py-1 text-xs text-ember hover:bg-ember/10"
                >
                  Excluir
                </button>
              </div>
            </li>
          ))}
          {rags.length === 0 && (
            <li className="px-4 py-6 text-center text-sand/45">Nenhum RAG ainda.</li>
          )}
        </ul>
      </section>
    </div>
  );
}
