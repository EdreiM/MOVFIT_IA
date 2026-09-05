import { FormEvent, useEffect, useMemo, useState } from "react";
import { api } from "../api";
import { useAuth } from "../auth";

type Conversation = {
  id: string;
  contact_phone: string;
  contact_name: string | null;
  status: string;
  ai_enabled: boolean;
  last_message_at: string | null;
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

export default function ConversationsPage() {
  const { user } = useAuth();
  const isSuperAdmin = user?.role === "super_admin";
  const [items, setItems] = useState<Conversation[]>([]);
  const [selected, setSelected] = useState<Conversation | null>(null);
  const [messages, setMessages] = useState<Message[]>([]);
  const [reply, setReply] = useState("");
  const [error, setError] = useState("");
  const [search, setSearch] = useState("");

  const load = () =>
    api<Conversation[]>("/conversations")
      .then(setItems)
      .catch((e) => setError(e.message));

  useEffect(() => {
    load();
  }, []);

  const filtered = useMemo(() => {
    const q = search.trim().toLowerCase();
    if (!q) return items;
    return items.filter(
      (c) =>
        c.id.toLowerCase().includes(q) ||
        c.contact_phone.toLowerCase().includes(q) ||
        (c.contact_name || "").toLowerCase().includes(q)
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

  return (
    <div className="space-y-6">
      <div>
        <h2 className="font-display text-3xl font-bold">Conversas</h2>
        <p className="mt-1 text-sand/55">Histórico e intervenção humana.</p>
      </div>
      {error && <p className="text-ember">{error}</p>}

      <div className="grid gap-4 lg:grid-cols-[320px_1fr]">
        <div className="space-y-2">
          <input
            className="w-full rounded-md border border-white/15 bg-ink px-3 py-2 text-sm"
            placeholder="Buscar por nome, telefone ou ID..."
            value={search}
            onChange={(e) => setSearch(e.target.value)}
          />
          <ul className="max-h-[70vh] overflow-auto border border-white/10">
            {filtered.map((c) => (
              <li key={c.id}>
                <button
                  onClick={() => open(c)}
                  className={`w-full border-b border-white/10 px-4 py-3 text-left transition hover:bg-white/5 ${
                    selected?.id === c.id ? "bg-leaf/15 border-l-2 border-leaf" : ""
                  }`}
                >
                  <p className="font-medium">{c.contact_name || c.contact_phone}</p>
                  <p className="text-xs text-sand/50">
                    {c.status} · IA {c.ai_enabled ? "ligada" : "pausada"}
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

        <div className="border border-white/10 bg-ink/30">
          {!selected ? (
            <p className="p-8 text-sand/45">Selecione uma conversa.</p>
          ) : (
            <div className="flex h-full min-h-[70vh] flex-col">
              <div className="flex items-center justify-between border-b border-white/10 px-4 py-3">
                <div>
                  <p className="font-medium">{selected.contact_name || selected.contact_phone}</p>
                  <p className="text-xs text-sand/50">{selected.contact_phone}</p>
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
              <div className="flex-1 space-y-3 overflow-auto p-4">
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
    </div>
  );
}
