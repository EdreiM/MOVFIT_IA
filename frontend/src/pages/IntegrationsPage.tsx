import { FormEvent, useEffect, useState } from "react";
import { api, API_URL } from "../api";

type Integration = {
  id: string;
  name: string;
  integration_type: string;
  adapter_key: string;
  outbound_url: string | null;
  field_mapping: Record<string, string> | null;
  is_active: boolean;
};

// Campos que o adaptador genérico entende (app/adapters/generic_mapping.py) —
// o valor é o caminho (estilo "data.message.text") dentro do JSON que a
// plataforma externa manda no webhook. O que ficar em branco usa o default
// já embutido no backend (mostrado no placeholder).
const GENERIC_MAPPING_FIELDS: { key: string; label: string; placeholder: string; hint?: string }[] = [
  { key: "text", label: "Texto da mensagem", placeholder: "text" },
  { key: "contact_phone", label: "Telefone do contato", placeholder: "from" },
  { key: "channel_to", label: "Número/canal de destino", placeholder: "to", hint: "opcional — só se a plataforma informar pra qual número/canal foi" },
  { key: "external_conversation_id", label: "ID da conversa na plataforma", placeholder: "conversation_id" },
  { key: "external_message_id", label: "ID da mensagem na plataforma", placeholder: "id", hint: "opcional" },
  { key: "event_type", label: "Tipo do evento", placeholder: "event_type", hint: 'valor deve virar "message_inbound" pra IA responder — mapeie ou fixe conforme o evento que você marcar na plataforma' },
  { key: "actor", label: "Quem enviou", placeholder: "actor", hint: 'opcional — "customer" quando ausente' },
  { key: "content_type", label: "Tipo de conteúdo", placeholder: "content_type", hint: 'opcional — "text" quando ausente' },
  { key: "timestamp", label: "Data/hora do evento", placeholder: "timestamp", hint: "opcional, formato ISO" },
];

const emptyMapping = () => Object.fromEntries(GENERIC_MAPPING_FIELDS.map((f) => [f.key, ""]));

export default function IntegrationsPage() {
  const [items, setItems] = useState<Integration[]>([]);
  const [name, setName] = useState("Evolution MOVFIT_IA");
  const [adapter, setAdapter] = useState("evolution_api_v1");
  const [outbound, setOutbound] = useState("https://evolutiongo.xmov.com.br");
  const [instanceName, setInstanceName] = useState("MOVFIT_IA");
  const [instanceToken, setInstanceToken] = useState("");
  const [phoneNumber, setPhoneNumber] = useState("+5593936180433");
  const [mapping, setMapping] = useState<Record<string, string>>(emptyMapping());
  const [error, setError] = useState("");
  const [created, setCreated] = useState<Integration | null>(null);
  const [editingId, setEditingId] = useState<string | null>(null);

  const load = () =>
    api<Integration[]>("/integrations")
      .then(setItems)
      .catch((e) => setError(e.message));

  useEffect(() => {
    load();
  }, []);

  const onDelete = async (id: string, itemName: string) => {
    if (!confirm(`Excluir a integração "${itemName}"? Essa ação não pode ser desfeita.`)) return;
    setError("");
    try {
      await api(`/integrations/${id}`, { method: "DELETE" });
      await load();
    } catch (err) {
      setError(err instanceof Error ? err.message : "Erro");
    }
  };

  const onMappingChange = (key: string, value: string) =>
    setMapping((prev) => ({ ...prev, [key]: value }));

  const resetForm = () => {
    setEditingId(null);
    setName("Evolution MOVFIT_IA");
    setAdapter("evolution_api_v1");
    setOutbound("https://evolutiongo.xmov.com.br");
    setMapping(emptyMapping());
  };

  const onEdit = (integ: Integration) => {
    setEditingId(integ.id);
    setAdapter(integ.adapter_key);
    setName(integ.name);
    setOutbound(integ.outbound_url ?? "");
    setMapping({ ...emptyMapping(), ...(integ.field_mapping ?? {}) });
    window.scrollTo({ top: 0, behavior: "smooth" });
  };

  const onSubmit = async (e: FormEvent) => {
    e.preventDefault();
    setError("");

    if (editingId) {
      try {
        await api(`/integrations/${editingId}`, {
          method: "PATCH",
          body: JSON.stringify({
            name,
            outbound_url: outbound || null,
            field_mapping: Object.fromEntries(Object.entries(mapping).filter(([, v]) => v.trim())),
          }),
        });
        resetForm();
        await load();
      } catch (err) {
        setError(err instanceof Error ? err.message : "Erro ao salvar integração");
      }
      return;
    }

    try {
      const integ = await api<Integration>("/integrations/webhook", {
        method: "POST",
        body: JSON.stringify({
          name,
          adapter_key: adapter,
          outbound_url: outbound || null,
          config:
            adapter === "evolution_api_v1"
              ? {
                  base_url: outbound || null,
                  instance_name: instanceName,
                  instance_token: instanceToken,
                  phone_number: phoneNumber,
                }
              : {},
          field_mapping:
            adapter === "generic_mapping"
              ? Object.fromEntries(Object.entries(mapping).filter(([, v]) => v.trim()))
              : {},
        }),
      });
      if (adapter === "evolution_api_v1") {
        setInstanceToken("");
      }
      if (adapter === "generic_mapping") {
        setCreated(integ);
        setMapping(emptyMapping());
      }
      await load();
    } catch (err) {
      setError(err instanceof Error ? err.message : "Erro");
    }
  };

  return (
    <div className="space-y-8">
      <div>
        <h2 className="font-display text-3xl font-bold">Integrações</h2>
        <p className="mt-1 text-sand/55">
          Webhooks inbound/outbound e adaptadores (Mov Fit Hub, genérico).
        </p>
      </div>

      <form
        onSubmit={onSubmit}
        className="grid gap-3 border border-white/10 bg-panel p-5 sm:grid-cols-2"
      >
        <input
          className="rounded-md border border-white/15 bg-ink px-3 py-2"
          value={name}
          onChange={(e) => setName(e.target.value)}
          placeholder="Nome"
          required
        />
        <select
          className="rounded-md border border-white/15 bg-ink px-3 py-2 disabled:opacity-50"
          value={adapter}
          onChange={(e) => setAdapter(e.target.value)}
          disabled={!!editingId}
        >
          <option value="evolution_api_v1">evolution_api_v1</option>
          <option value="movfit_hub_v1">movfit_hub_v1</option>
          <option value="generic_mapping">generic_mapping</option>
        </select>
        <input
          className="rounded-md border border-white/15 bg-ink px-3 py-2 sm:col-span-2"
          value={outbound}
          onChange={(e) => setOutbound(e.target.value)}
          placeholder="URL base / outbound"
        />
        {adapter === "evolution_api_v1" && (
          <>
            <input
              className="rounded-md border border-white/15 bg-ink px-3 py-2"
              value={instanceName}
              onChange={(e) => setInstanceName(e.target.value)}
              placeholder="Nome da instância"
              required
            />
            <input
              className="rounded-md border border-white/15 bg-ink px-3 py-2"
              value={phoneNumber}
              onChange={(e) => setPhoneNumber(e.target.value)}
              placeholder="+55 número da instância"
              required
            />
            <input
              className="rounded-md border border-white/15 bg-ink px-3 py-2 sm:col-span-2"
              type="password"
              value={instanceToken}
              onChange={(e) => setInstanceToken(e.target.value)}
              placeholder="Token da instância Evolution"
              required
            />
          </>
        )}
        {adapter === "generic_mapping" && (
          <div className="space-y-3 sm:col-span-2">
            <p className="text-sm text-sand/55">
              Conecte qualquer plataforma que manda webhook em JSON: diga em qual campo do payload
              dela está cada informação (caminho estilo <code className="text-lime">data.message.text</code>{" "}
              pra campos aninhados). Deixe em branco o que não existir — o padrão em cinza já cobre o
              formato mais comum. Você recebe a URL de inbound assim que criar, pra colar no campo
              "Url" da plataforma.
            </p>
            <div className="grid gap-2 sm:grid-cols-2">
              {GENERIC_MAPPING_FIELDS.map((f) => (
                <label key={f.key} className="block space-y-1">
                  <span className="text-xs text-sand/60">{f.label}</span>
                  <input
                    className="w-full rounded-md border border-white/15 bg-ink px-2 py-1.5 text-sm"
                    placeholder={f.placeholder}
                    value={mapping[f.key] ?? ""}
                    onChange={(e) => onMappingChange(f.key, e.target.value)}
                  />
                  {f.hint && <span className="block text-[11px] text-sand/40">{f.hint}</span>}
                </label>
              ))}
            </div>
          </div>
        )}
        <div className="flex items-center gap-3 sm:col-span-2">
          <button type="submit" className="rounded-md bg-leaf px-4 py-2 font-semibold text-white hover:bg-lime">
            {editingId ? "Salvar alterações" : "Criar integração"}
          </button>
          {editingId && (
            <button
              type="button"
              onClick={resetForm}
              className="rounded-md border border-white/15 px-4 py-2 font-semibold text-sand/60 hover:bg-white/5"
            >
              Cancelar
            </button>
          )}
        </div>
      </form>

      {created && (
        <div className="border border-leaf/40 bg-leaf/10 p-4">
          <p className="font-medium text-lime">
            Integração "{created.name}" criada! Cole esta URL no campo de webhook da plataforma
            externa (e marque lá o evento de mensagem recebida):
          </p>
          <p className="mt-2 break-all rounded-md bg-ink px-3 py-2 text-sm text-lime">
            {API_URL}/webhooks/inbound/{created.id}
          </p>
          <button
            type="button"
            onClick={() => setCreated(null)}
            className="mt-2 text-xs text-sand/50 hover:text-sand"
          >
            Ok, entendi
          </button>
        </div>
      )}

      {error && <p className="text-ember">{error}</p>}

      <ul className="space-y-3">
        {items.map((i) => (
          <li key={i.id} className="border border-white/10 px-4 py-3">
            <div className="flex items-start justify-between gap-3">
              <p className="font-medium">
                {i.name}{" "}
                <span className="text-sm text-sand/50">({i.adapter_key})</span>
              </p>
              <div className="flex shrink-0 items-center gap-2">
                {i.adapter_key === "generic_mapping" && (
                  <button
                    type="button"
                    onClick={() => onEdit(i)}
                    className="rounded-md border border-white/15 px-2 py-1 text-xs text-sand/60 hover:bg-white/5"
                  >
                    Editar
                  </button>
                )}
                <button
                  type="button"
                  onClick={() => onDelete(i.id, i.name)}
                  className="rounded-md border border-ember/40 px-2 py-1 text-xs text-ember hover:bg-ember/10"
                >
                  Excluir
                </button>
              </div>
            </div>
            <p className="mt-1 break-all text-sm text-lime">
              Inbound: {API_URL}/webhooks/inbound/{i.id}
            </p>
            {i.outbound_url && (
              <p className="mt-1 break-all text-sm text-sand/50">Outbound: {i.outbound_url}</p>
            )}
            {i.field_mapping && Object.keys(i.field_mapping).length > 0 && (
              <p className="mt-1 text-xs text-sand/40">
                Mapeamento: {Object.entries(i.field_mapping).map(([k, v]) => `${k} ← ${v}`).join(", ")}
              </p>
            )}
          </li>
        ))}
        {items.length === 0 && (
          <li className="text-center text-sand/45">Nenhuma integração. O seed cria a Mov Fit Hub.</li>
        )}
      </ul>
    </div>
  );
}
