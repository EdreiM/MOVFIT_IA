import { useEffect, useState } from "react";
import { api } from "../api";

type WebhookLog = {
  id: string;
  direction: string;
  status: string;
  http_status: number | null;
  error_message: string | null;
  created_at: string | null;
  payload: Record<string, unknown> | null;
};

const statusStyle: Record<string, string> = {
  ok: "border-leaf/40 text-lime",
  error: "border-ember/40 text-ember",
  received: "border-white/20 text-sand/60",
};

export default function WebhookLogsPage() {
  const [items, setItems] = useState<WebhookLog[]>([]);
  const [error, setError] = useState("");
  const [onlyErrors, setOnlyErrors] = useState(true);
  const [expandedId, setExpandedId] = useState<string | null>(null);
  const [search, setSearch] = useState("");

  const load = () =>
    api<WebhookLog[]>("/admin/webhooks/logs?limit=100")
      .then(setItems)
      .catch((e) => setError(e.message));

  useEffect(() => {
    load();
  }, []);

  const filtered = items.filter((l) => {
    if (onlyErrors && l.status !== "error") return false;
    if (search.trim() && !JSON.stringify(l.payload ?? {}).toLowerCase().includes(search.trim().toLowerCase()))
      return false;
    return true;
  });

  return (
    <div className="space-y-8">
      <div>
        <h2 className="font-display text-3xl font-bold">Logs de Webhook</h2>
        <p className="mt-1 text-sand/55">
          Últimas mensagens recebidas/enviadas via integração — útil pra achar por que uma mensagem
          específica não foi processada.
        </p>
      </div>

      {error && <p className="text-ember">{error}</p>}

      <div className="flex flex-wrap items-center gap-3">
        <label className="flex items-center gap-2 text-sm text-sand/70">
          <input
            type="checkbox"
            checked={onlyErrors}
            onChange={(e) => setOnlyErrors(e.target.checked)}
          />
          Só com erro
        </label>
        <input
          className="rounded-md border border-white/15 bg-ink px-3 py-2 text-sm"
          placeholder="Buscar no payload (telefone, sessionId...)"
          value={search}
          onChange={(e) => setSearch(e.target.value)}
        />
        <button
          type="button"
          onClick={load}
          className="rounded-md border border-white/15 px-3 py-2 text-sm text-sand/70 hover:bg-white/5"
        >
          Atualizar
        </button>
      </div>

      <ul className="divide-y divide-white/10 border border-white/10">
        {filtered.map((l) => (
          <li key={l.id} className="px-4 py-3">
            <button
              type="button"
              onClick={() => setExpandedId(expandedId === l.id ? null : l.id)}
              className="flex w-full items-center justify-between gap-3 text-left"
            >
              <div className="flex items-center gap-3">
                <span
                  className={`rounded-md border px-2 py-0.5 text-xs uppercase ${statusStyle[l.status] ?? "border-white/20 text-sand/60"}`}
                >
                  {l.status}
                </span>
                <span className="text-xs text-sand/50">{l.direction}</span>
                <span className="text-xs text-sand/40">HTTP {l.http_status ?? "-"}</span>
                <span className="text-sm text-sand/70">
                  {l.created_at ? new Date(l.created_at).toLocaleString("pt-BR") : "-"}
                </span>
              </div>
              <span className="text-xs text-sand/40">{expandedId === l.id ? "fechar ▲" : "detalhes ▼"}</span>
            </button>
            {l.error_message && (
              <p className="mt-1 text-sm text-ember">{l.error_message}</p>
            )}
            {expandedId === l.id && (
              <pre className="mt-2 max-h-96 overflow-auto rounded-md border border-white/10 bg-ink px-3 py-2 text-xs text-sand/80">
                {JSON.stringify(l.payload, null, 2)}
              </pre>
            )}
          </li>
        ))}
        {filtered.length === 0 && (
          <li className="px-4 py-8 text-center text-sand/45">Nenhum log encontrado.</li>
        )}
      </ul>
    </div>
  );
}
