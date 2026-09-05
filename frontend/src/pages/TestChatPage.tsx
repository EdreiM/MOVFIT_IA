import { FormEvent, useEffect, useRef, useState } from "react";
import { api } from "../api";
import { useAuth } from "../auth";

type Message = {
  id: string;
  conversation_id: string;
  direction: string;
  actor: string;
  content_type: string;
  text: string | null;
  raw_payload?: { images?: { unidade: string; plano: string; url: string }[] } | null;
  created_at: string;
};

type AiConfig = {
  ai_name: string;
};

type Integration = {
  id: string;
  name: string;
};

const POLL_INTERVAL_MS = 1200;
const POLL_MAX_ATTEMPTS = 40; // ~48s: cobre o debounce (padrão 8s) + latência da LLM + follow-ups rápidos

function sleep(ms: number) {
  return new Promise((resolve) => setTimeout(resolve, ms));
}

export default function TestChatPage() {
  const { companyId } = useAuth();
  const [messages, setMessages] = useState<Message[]>([]);
  const [aiName, setAiName] = useState("a IA");
  const [integrations, setIntegrations] = useState<Integration[]>([]);
  const [integrationId, setIntegrationId] = useState("");
  const [text, setText] = useState("");
  const [posting, setPosting] = useState(false);
  const [awaitingReply, setAwaitingReply] = useState(false);
  const [error, setError] = useState("");
  const bottomRef = useRef<HTMLDivElement>(null);
  const pollingRef = useRef(false);

  const load = async () => {
    if (!companyId) return [];
    const list = await api<Message[]>(`/ai-configs/${companyId}/test-chat/messages`);
    setMessages(list);
    return list;
  };

  useEffect(() => {
    if (!companyId) return;
    load().catch((e) => setError(e.message));
    api<AiConfig>(`/ai-configs/${companyId}`)
      .then((cfg) => setAiName(cfg.ai_name || "a IA"))
      .catch(() => {});
    api<Integration[]>(`/integrations`)
      .then(setIntegrations)
      .catch(() => {});
  }, [companyId]);

  useEffect(() => {
    bottomRef.current?.scrollIntoView({ behavior: "smooth" });
  }, [messages, awaitingReply]);

  // Depois que o cliente manda mensagem(ns), a Mônica só responde após um
  // período de silêncio (debounce, agregando rajadas). Por isso a resposta
  // não vem na hora — fica esperando aparecer via polling.
  const pollForReply = async () => {
    if (pollingRef.current || !companyId) return;
    pollingRef.current = true;
    setAwaitingReply(true);
    try {
      for (let i = 0; i < POLL_MAX_ATTEMPTS; i++) {
        await sleep(POLL_INTERVAL_MS);
        const list = await load();
        const last = list[list.length - 1];
        if (!last || last.actor !== "customer") break;
      }
    } catch (err) {
      setError(err instanceof Error ? err.message : "Erro ao aguardar resposta");
    } finally {
      pollingRef.current = false;
      setAwaitingReply(false);
    }
  };

  const onSend = async (e: FormEvent) => {
    e.preventDefault();
    if (!companyId || !text.trim() || posting) return;
    setError("");
    const outgoing = text.trim();
    setText("");
    setPosting(true);
    // Otimista: mostra a mensagem do "cliente" na hora.
    setMessages((prev) => [
      ...prev,
      {
        id: `pending-${Date.now()}`,
        conversation_id: "pending",
        direction: "inbound",
        actor: "customer",
        content_type: "text",
        text: outgoing,
        created_at: new Date().toISOString(),
      },
    ]);
    try {
      await api(`/ai-configs/${companyId}/test-chat`, {
        method: "POST",
        body: JSON.stringify({ text: outgoing, integration_id: integrationId || null }),
      });
      pollForReply();
    } catch (err) {
      setError(err instanceof Error ? err.message : "Erro ao enviar mensagem");
      await load();
    } finally {
      setPosting(false);
    }
  };

  const onReset = async () => {
    if (!companyId) return;
    if (!confirm("Limpar o histórico do chat de teste?")) return;
    setError("");
    try {
      await api(`/ai-configs/${companyId}/test-chat`, { method: "DELETE" });
      setMessages([]);
    } catch (err) {
      setError(err instanceof Error ? err.message : "Erro ao limpar");
    }
  };

  if (!companyId) {
    return <p className="text-sand/60">Selecione uma empresa.</p>;
  }

  return (
    <div className="space-y-6">
      <div className="flex items-start justify-between gap-4">
        <div>
          <h2 className="font-display text-3xl font-bold">Chat de teste</h2>
          <p className="mt-1 text-sand/55">
            Converse com {aiName} aqui pra validar prompt, RAGs e comportamento — nada é
            enviado pelo WhatsApp real. Manda mais de uma mensagem seguida pra testar o
            agrupamento: ela espera você parar de digitar antes de responder tudo junto.
          </p>
        </div>
        <button
          type="button"
          onClick={onReset}
          className="shrink-0 rounded-md border border-white/15 px-3 py-2 text-sm text-sand/70 hover:border-ember/40 hover:text-ember"
        >
          Limpar conversa
        </button>
      </div>

      <label className="block max-w-sm space-y-1">
        <span className="text-sm text-sand/60">
          Simular integração{" "}
          <span className="text-sand/40">
            (ferramentas cadastradas só pra uma integração específica só aparecem pra IA
            aqui se você selecionar essa mesma integração)
          </span>
        </span>
        <select
          className="w-full rounded-md border border-white/15 bg-ink px-3 py-2"
          value={integrationId}
          onChange={(e) => setIntegrationId(e.target.value)}
        >
          <option value="">Nenhuma (só ferramentas globais)</option>
          {integrations.map((i) => (
            <option key={i.id} value={i.id}>
              {i.name}
            </option>
          ))}
        </select>
      </label>

      <div className="flex h-[60vh] flex-col border border-white/10 bg-panel">
        <div className="flex-1 space-y-3 overflow-y-auto p-4">
          {messages.length === 0 && (
            <p className="text-center text-sand/45">
              Manda uma mensagem pra começar a testar a IA.
            </p>
          )}
          {messages.map((m) => {
            const fromCustomer = m.actor === "customer";
            const images = m.content_type === "image" ? m.raw_payload?.images ?? [] : [];
            if (images.length > 0) {
              return (
                <div key={m.id} className={`flex flex-wrap gap-2 ${fromCustomer ? "justify-end" : "justify-start"}`}>
                  {images.map((img, i) => (
                    <a key={i} href={img.url} target="_blank" rel="noreferrer" title={`${img.plano} — ${img.unidade}`}>
                      <img
                        src={img.url}
                        alt={`${img.plano} — ${img.unidade}`}
                        className="h-32 w-32 rounded-md object-cover"
                      />
                    </a>
                  ))}
                </div>
              );
            }
            return (
              <div
                key={m.id}
                className={`flex ${fromCustomer ? "justify-end" : "justify-start"}`}
              >
                <div
                  className={`max-w-[75%] whitespace-pre-wrap rounded-lg px-3 py-2 text-sm ${
                    fromCustomer
                      ? "bg-leaf text-white"
                      : "border border-white/10 bg-ink text-sand"
                  }`}
                >
                  {m.text}
                </div>
              </div>
            );
          })}
          {awaitingReply && (
            <div className="flex justify-start">
              <div className="max-w-[75%] rounded-lg border border-white/10 bg-ink px-3 py-2 text-sm text-sand/50">
                {aiName} está digitando…
              </div>
            </div>
          )}
          <div ref={bottomRef} />
        </div>

        <form onSubmit={onSend} className="flex gap-2 border-t border-white/10 p-3">
          <input
            className="flex-1 rounded-md border border-white/15 bg-ink px-3 py-2"
            placeholder="Digite como se fosse o cliente…"
            value={text}
            onChange={(e) => setText(e.target.value)}
          />
          <button
            type="submit"
            disabled={posting || !text.trim()}
            className="rounded-md bg-leaf px-4 py-2 font-semibold text-white hover:bg-lime disabled:opacity-50"
          >
            Enviar
          </button>
        </form>
      </div>

      {error && <p className="text-ember">{error}</p>}
    </div>
  );
}
