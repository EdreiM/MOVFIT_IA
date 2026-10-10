import { FormEvent, useCallback, useEffect, useMemo, useState } from "react";
import { api, apiDownload } from "../api";
import { useAuth } from "../auth";

type Conversation = {
  id: string;
  contact_phone: string;
  contact_name: string | null;
  status: string;
  ai_enabled: boolean;
  last_message_at: string | null;
  last_message_preview?: string | null;
  last_message_actor?: string | null;
  external_conversation_id?: string | null;
  handoff_summary?: string | null;
};

type Message = {
  id: string;
  direction: string;
  actor: string;
  content_type: string;
  text: string | null;
  raw_payload?: { images?: { unidade: string; plano: string; url: string }[] } | null;
  created_at: string;
};

const STATUS_LABELS: Record<string, string> = {
  open: "Aberta",
  with_human: "Com atendente",
  resolved: "Encerrada",
};

function actorLabel(actor: string | null | undefined): string {
  if (actor === "customer") return "Cliente";
  if (actor === "human_agent") return "Atendente";
  if (actor === "ai") return "IA";
  return actor || "";
}

export default function ConversationsPage() {
  const { user } = useAuth();
  const isSuperAdmin = user?.role === "super_admin";
  const [items, setItems] = useState<Conversation[]>([]);
  const [selected, setSelected] = useState<Conversation | null>(null);
  const [messages, setMessages] = useState<Message[]>([]);
  const [reply, setReply] = useState("");
  const [error, setError] = useState("");
  const [search, setSearch] = useState("");
  const [statusFilter, setStatusFilter] = useState("");
  const [aiFilter, setAiFilter] = useState<"" | "on" | "off">("");
  const [exportDays, setExportDays] = useState(7);
  const [exportMask, setExportMask] = useState(true);
  const [exporting, setExporting] = useState(false);
  const [exportInfo, setExportInfo] = useState("");

  const exportConversations = async () => {
    setError("");
    setExportInfo("");
    setExporting(true);
    try {
      const params = new URLSearchParams({ days: String(exportDays), mask: String(exportMask) });
      if (statusFilter) params.set("status", statusFilter);
      if (aiFilter === "on") params.set("ai_enabled", "true");
      if (aiFilter === "off") params.set("ai_enabled", "false");
      const total = await apiDownload(`/conversations/export?${params}`, "conversas.zip");
      setExportInfo(total === null ? "Arquivo baixado." : `${total} conversa(s) exportada(s).`);
    } catch (err) {
      setError(err instanceof Error ? err.message : "Erro ao exportar conversas");
    } finally {
      setExporting(false);
    }
  };

  const load = useCallback(() => {
    const params = new URLSearchParams();
    if (statusFilter) params.set("status", statusFilter);
    if (aiFilter === "on") params.set("ai_enabled", "true");
    if (aiFilter === "off") params.set("ai_enabled", "false");
    const q = search.trim();
    if (q) params.set("q", q);
    const qs = params.toString();
    return api<Conversation[]>(`/conversations${qs ? `?${qs}` : ""}`)
      .then(setItems)
      .catch((e) => setError(e.message));
  }, [statusFilter, aiFilter, search]);

  useEffect(() => {
    const timer = window.setTimeout(() => {
      load();
    }, search.trim() ? 350 : 0);
    return () => window.clearTimeout(timer);
  }, [load, search]);

  const filtered = useMemo(() => {
    const q = search.trim().toLowerCase();
    if (!q) return items;
    return items.filter(
      (c) =>
        c.id.toLowerCase().includes(q) ||
        c.contact_phone.toLowerCase().includes(q) ||
        (c.contact_name || "").toLowerCase().includes(q) ||
        (c.last_message_preview || "").toLowerCase().includes(q)
    );
  }, [items, search]);

  const open = async (c: Conversation) => {
    setSelected(c);
    const msgs = await api<Message[]>(`/conversations/${c.id}/messages`);
    setMessages(msgs);
  };

  const toggleAi = async () => {
    if (!selected) return;
    const updated = await api<Conversation>(`/conversations/${selected.id}/ai-status`, {
      method: "PATCH",
      body: JSON.stringify({ ai_enabled: !selected.ai_enabled }),
    });
    setSelected(updated);
    await load();
  };

  const deleteConversation = async () => {
    if (!selected) return;
    if (!confirm(`Excluir a conversa com "${selected.contact_name || selected.contact_phone}"? Essa ação não pode ser desfeita.`))
      return;
    setError("");
    try {
      await api(`/conversations/${selected.id}`, { method: "DELETE" });
      setSelected(null);
      setMessages([]);
      await load();
    } catch (err) {
      setError(err instanceof Error ? err.message : "Erro ao excluir conversa");
    }
  };

  const send = async (e: FormEvent) => {
    e.preventDefault();
    if (!selected || !reply.trim()) return;
    await api(`/conversations/${selected.id}/messages`, {
      method: "POST",
      body: JSON.stringify({ text: reply }),
    });
    setReply("");
    await open(selected);
    await load();
  };

  // No desktop a grade ocupa o que sobra da janela (cabeçalho ≈ 12.25rem); lista e mensagens rolam por dentro.
  // O título fica na coluna da lista pra conversa aproveitar a altura toda.
  return (
    <div className="grid gap-4 lg:h-[calc(100vh-12.25rem)] lg:min-h-[34rem] lg:grid-cols-[320px_1fr] lg:grid-rows-[minmax(0,1fr)]">
      <div className="flex min-h-0 flex-col gap-2">
        <div className="mb-2">
          <h2 className="font-display text-3xl font-bold">Conversas</h2>
          <p className="mt-1 text-sand/55">Histórico e intervenção humana.</p>
        </div>
        {error && <p className="text-ember">{error}</p>}
        <input
          className="w-full rounded-md border border-white/15 bg-ink px-3 py-2 text-sm"
          placeholder="Buscar nome, telefone ou texto…"
          value={search}
          onChange={(e) => setSearch(e.target.value)}
        />
        <div className="flex flex-wrap gap-2">
          <select
            className="flex-1 rounded-md border border-white/15 bg-ink px-2 py-2 text-sm"
            value={statusFilter}
            onChange={(e) => setStatusFilter(e.target.value)}
          >
            <option value="">Todos os status</option>
            <option value="open">Abertas</option>
            <option value="with_human">Com atendente</option>
            <option value="resolved">Encerradas</option>
          </select>
          <select
            className="flex-1 rounded-md border border-white/15 bg-ink px-2 py-2 text-sm"
            value={aiFilter}
            onChange={(e) => setAiFilter(e.target.value as "" | "on" | "off")}
          >
            <option value="">IA: todas</option>
            <option value="on">IA ligada</option>
            <option value="off">IA pausada</option>
          </select>
        </div>
        <details className="rounded-md border border-white/10 px-3 py-2 text-sm">
          <summary className="cursor-pointer text-sand/70">Exportar conversas (.zip)</summary>
          <div className="mt-2 space-y-2">
            <p className="text-xs text-sand/50">
              Baixa as conversas do período (respeitando os filtros acima) em texto, com as chamadas
              de ferramenta — pra revisar onde a IA errou.
            </p>
            <div className="flex items-center gap-2">
              <select
                className="flex-1 rounded-md border border-white/15 bg-ink px-2 py-1.5 text-sm"
                value={exportDays}
                onChange={(e) => setExportDays(Number(e.target.value))}
              >
                <option value={1}>Último dia</option>
                <option value={7}>Últimos 7 dias</option>
                <option value={30}>Últimos 30 dias</option>
                <option value={90}>Últimos 90 dias</option>
              </select>
            </div>
            <label className="flex items-center gap-2 text-xs text-sand/70">
              <input
                type="checkbox"
                checked={exportMask}
                onChange={(e) => setExportMask(e.target.checked)}
              />
              Ocultar CPF, telefone e e-mail
            </label>
            <button
              type="button"
              onClick={exportConversations}
              disabled={exporting}
              className="w-full rounded-md bg-leaf px-3 py-2 text-sm font-semibold text-white hover:bg-lime disabled:opacity-50"
            >
              {exporting ? "Gerando…" : "Baixar .zip"}
            </button>
            {exportInfo && <p className="text-xs text-lime">{exportInfo}</p>}
          </div>
        </details>
        <ul className="max-h-[70vh] overflow-auto border border-white/10 lg:max-h-none lg:min-h-0 lg:flex-1">
          {filtered.map((c) => (
            <li key={c.id}>
              <button
                onClick={() => open(c)}
                className={`w-full border-b border-white/10 px-4 py-3 text-left transition hover:bg-white/5 ${
                  selected?.id === c.id ? "bg-leaf/15 border-l-2 border-leaf" : ""
                }`}
              >
                <p className="font-medium">{c.contact_name || c.contact_phone}</p>
                {c.last_message_preview && (
                  <p className="mt-1 line-clamp-2 text-xs text-sand/60">
                    {actorLabel(c.last_message_actor)}: {c.last_message_preview}
                  </p>
                )}
                <p className="mt-1 text-xs text-sand/50">
                  {STATUS_LABELS[c.status] || c.status} · IA {c.ai_enabled ? "ligada" : "pausada"}
                  {c.last_message_at &&
                    ` · ${new Date(c.last_message_at).toLocaleString("pt-BR", {
                      day: "2-digit",
                      month: "2-digit",
                      hour: "2-digit",
                      minute: "2-digit",
                    })}`}
                </p>
              </button>
            </li>
          ))}
          {filtered.length === 0 && (
            <li className="px-4 py-8 text-center text-sand/45">
              {items.length === 0 ? "Nenhuma conversa." : "Nenhuma conversa bate com a busca."}
            </li>
          )}
        </ul>
      </div>

      <div className="flex min-h-0 flex-col border border-white/10 bg-ink/30">
        {!selected ? (
          <p className="p-8 text-sand/45">Selecione uma conversa.</p>
        ) : (
          <div className="flex h-[70vh] flex-col lg:h-auto lg:min-h-0 lg:flex-1">
            <div className="flex items-center justify-between border-b border-white/10 px-4 py-3">
              <div>
                <p className="font-medium">{selected.contact_name || selected.contact_phone}</p>
                <p className="text-xs text-sand/50">
                  {selected.contact_name && `${selected.contact_phone} · `}
                  {selected.external_conversation_id ? (
                    <span className="text-sand/40" title="sessionId WTS/GYMBOT — necessário pro envio no WhatsApp">
                      Sessão: {selected.external_conversation_id}
                    </span>
                  ) : (
                    <span className="text-ember/80">
                      Sem sessão WTS — respostas podem não chegar no WhatsApp até o cliente mandar msg de novo.
                    </span>
                  )}
                </p>
              </div>
              <div className="flex shrink-0 items-center gap-2">
                <button
                  onClick={toggleAi}
                  className={`rounded-md px-3 py-1.5 text-sm ${
                    selected.ai_enabled ? "bg-leaf text-white" : "bg-white/10 text-muted"
                  }`}
                >
                  IA {selected.ai_enabled ? "ligada" : "pausada"}
                </button>
                {isSuperAdmin && (
                  <button
                    onClick={deleteConversation}
                    title="Só visível pra super_admin — apagar conversa de teste/dev"
                    className="rounded-md border border-ember/40 px-3 py-1.5 text-sm text-ember hover:bg-ember/10"
                  >
                    Excluir
                  </button>
                )}
              </div>
            </div>
            {selected.handoff_summary && (
              <details className="border-b border-white/10 bg-leaf/5 px-4 py-2 text-xs text-sand/70">
                <summary className="cursor-pointer text-sm text-sand/85">
                  Resumo da transferência pro atendente
                </summary>
                <p className="mt-2 whitespace-pre-line">{selected.handoff_summary}</p>
              </details>
            )}
            <div className="min-h-0 flex-1 space-y-3 overflow-auto p-4">
              {messages.map((m) => {
                const images = m.content_type === "image" ? m.raw_payload?.images ?? [] : [];
                return (
                  <div
                    key={m.id}
                    className={`max-w-[80%] rounded-lg px-3 py-2 text-sm ${
                      m.direction === "inbound"
                        ? "bg-white/10"
                        : "ml-auto bg-leaf text-white"
                    }`}
                  >
                    <p className="mb-1 text-[10px] uppercase tracking-wide opacity-60">
                      {m.actor}
                    </p>
                    {images.length > 0 ? (
                      <div className="flex flex-wrap gap-2">
                        {images.map((img, i) => (
                          <a key={i} href={img.url} target="_blank" rel="noreferrer" title={`${img.plano} — ${img.unidade}`}>
                            <img
                              src={img.url}
                              alt={`${img.plano} — ${img.unidade}`}
                              className="h-24 w-24 rounded-md border border-white/10 object-cover"
                            />
                          </a>
                        ))}
                      </div>
                    ) : (
                      <p>{m.text}</p>
                    )}
                  </div>
                );
              })}
            </div>
            <form onSubmit={send} className="flex gap-2 border-t border-white/10 p-3">
              <input
                className="flex-1 rounded-md border border-white/15 bg-ink px-3 py-2"
                placeholder="Responder como humano (pausa a IA)…"
                value={reply}
                onChange={(e) => setReply(e.target.value)}
              />
              <button type="submit" className="rounded-md bg-leaf px-4 py-2 font-semibold text-white hover:bg-lime">
                Enviar
              </button>
            </form>
          </div>
        )}
      </div>
    </div>
  );
}
