import { FormEvent, useEffect, useState } from "react";
import { api } from "../api";

type NumberItem = {
  id: string;
  label: string;
  phone: string;
  channel_type: string;
  is_active: boolean;
  webhook_mode: boolean;
};

export default function NumbersPage() {
  const [items, setItems] = useState<NumberItem[]>([]);
  const [label, setLabel] = useState("");
  const [phone, setPhone] = useState("");
  const [channel, setChannel] = useState("whatsapp_qr");
  const [error, setError] = useState("");

  const load = () =>
    api<NumberItem[]>("/numbers")
      .then(setItems)
      .catch((e) => setError(e.message));

  useEffect(() => {
    load();
  }, []);

  const onSubmit = async (e: FormEvent) => {
    e.preventDefault();
    setError("");
    try {
      await api("/numbers", {
        method: "POST",
        body: JSON.stringify({
          label,
          phone,
          channel_type: channel,
        }),
      });
      setLabel("");
      setPhone("");
      await load();
    } catch (err) {
      setError(err instanceof Error ? err.message : "Erro");
    }
  };

  const toggle = async (n: NumberItem) => {
    await api(`/numbers/${n.id}`, {
      method: "PATCH",
      body: JSON.stringify({ is_active: !n.is_active }),
    });
    await load();
  };

  const onDelete = async (n: NumberItem) => {
    if (!confirm(`Excluir o número/canal "${n.label}"?`)) return;
    setError("");
    try {
      await api(`/numbers/${n.id}`, { method: "DELETE" });
      await load();
    } catch (err) {
      setError(err instanceof Error ? err.message : "Erro ao excluir");
    }
  };

  return (
    <div className="space-y-8">
      <div>
        <h2 className="font-display text-3xl font-bold">Números / Canais</h2>
        <p className="mt-1 text-sand/55">Adicione canais WhatsApp (Evolution QR ou Meta).</p>
      </div>

      <form
        onSubmit={onSubmit}
        className="grid gap-3 border border-white/10 bg-panel p-5 sm:grid-cols-4"
      >
        <input
          className="rounded-md border border-white/15 bg-ink px-3 py-2"
          placeholder="Rótulo"
          value={label}
          onChange={(e) => setLabel(e.target.value)}
          required
        />
        <input
          className="rounded-md border border-white/15 bg-ink px-3 py-2"
          placeholder="+55..."
          value={phone}
          onChange={(e) => setPhone(e.target.value)}
          required
        />
        <select
          className="rounded-md border border-white/15 bg-ink px-3 py-2"
          value={channel}
          onChange={(e) => setChannel(e.target.value)}
        >
          <option value="whatsapp_qr">WhatsApp QR (Evolution)</option>
          <option value="whatsapp_meta">WhatsApp Meta</option>
          <option value="webhook_generico">Webhook genérico</option>
        </select>
        <button type="submit" className="rounded-md bg-leaf px-4 py-2 font-semibold text-white hover:bg-lime">
          Adicionar
        </button>
      </form>

      {error && <p className="text-ember">{error}</p>}

      <ul className="divide-y divide-white/10 border border-white/10">
        {items.map((n) => (
          <li key={n.id} className="flex flex-wrap items-center justify-between gap-3 px-4 py-3">
            <div>
              <p className="font-medium">{n.label}</p>
              <p className="text-sm text-sand/55">
                {n.phone} · {n.channel_type}
              </p>
            </div>
            <div className="flex items-center gap-2">
              <button
                onClick={() => toggle(n)}
                className={`rounded-md px-3 py-1.5 text-sm ${
                  n.is_active ? "bg-leaf text-white" : "bg-white/5 text-muted"
                }`}
              >
                {n.is_active ? "Ativo" : "Inativo"}
              </button>
              <button
                onClick={() => onDelete(n)}
                className="rounded-md border border-ember/40 px-3 py-1.5 text-sm text-ember hover:bg-ember/10"
              >
                Excluir
              </button>
            </div>
          </li>
        ))}
        {items.length === 0 && (
          <li className="px-4 py-8 text-center text-sand/45">Nenhum número cadastrado.</li>
        )}
      </ul>
    </div>
  );
}
