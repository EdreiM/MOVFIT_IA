import { ChangeEvent, FormEvent, useEffect, useState } from "react";
import { api, apiUpload } from "../api";
import { useAuth } from "../auth";

type Unit = { id: string; name: string; city: string };

type Promotion = {
  id: string;
  title: string;
  message: string;
  image_url: string | null;
  is_active: boolean;
  valid_from: string | null;
  valid_until: string | null;
  audience: "all" | "women" | "men" | "new_students";
  mention_on_plan_request: boolean;
  unit_ids: string[];
  requires_transfer: boolean;
  transfer_reason: string | null;
  trigger_keywords: string[];
  sort_order: number;
};

type PromotionFormData = {
  title: string;
  message: string;
  valid_from: string;
  valid_until: string;
  audience: Promotion["audience"];
  mention_on_plan_request: boolean;
  unit_ids: string[];
  requires_transfer: boolean;
  transfer_reason: string;
  trigger_keywords: string;
  sort_order: string;
  is_active: boolean;
};

const AUDIENCE_LABELS: Record<Promotion["audience"], string> = {
  all: "Todos",
  women: "Mulheres",
  men: "Homens",
  new_students: "Novas matrículas",
};

const emptyForm = (): PromotionFormData => ({
  title: "",
  message: "",
  valid_from: "",
  valid_until: "",
  audience: "all",
  mention_on_plan_request: true,
  unit_ids: [],
  requires_transfer: true,
  transfer_reason: "",
  trigger_keywords: "",
  sort_order: "0",
  is_active: true,
});

function formFromPromotion(p: Promotion): PromotionFormData {
  return {
    title: p.title,
    message: p.message,
    valid_from: p.valid_from ?? "",
    valid_until: p.valid_until ?? "",
    audience: p.audience,
    mention_on_plan_request: p.mention_on_plan_request,
    unit_ids: p.unit_ids,
    requires_transfer: p.requires_transfer,
    transfer_reason: p.transfer_reason ?? "",
    trigger_keywords: p.trigger_keywords.join(", "),
    sort_order: String(p.sort_order),
    is_active: p.is_active,
  };
}

function payloadFromForm(data: PromotionFormData) {
  return {
    title: data.title.trim(),
    message: data.message.trim(),
    valid_from: data.valid_from || null,
    valid_until: data.valid_until || null,
    audience: data.audience,
    mention_on_plan_request: data.mention_on_plan_request,
    unit_ids: data.unit_ids,
    requires_transfer: data.requires_transfer,
    transfer_reason: data.transfer_reason.trim() || null,
    trigger_keywords: data.trigger_keywords
      .split(/[,;\n]+/)
      .map((k) => k.trim())
      .filter(Boolean),
    sort_order: Number(data.sort_order) || 0,
    is_active: data.is_active,
  };
}

function PromotionForm({
  initial,
  units,
  onSubmit,
  onCancel,
  onUploadImage,
  submitLabel,
}: {
  initial?: Promotion;
  units: Unit[];
  onSubmit: (data: PromotionFormData) => Promise<void>;
  onCancel?: () => void;
  onUploadImage?: (file: File) => Promise<string>;
  submitLabel: string;
}) {
  const [form, setForm] = useState<PromotionFormData>(initial ? formFromPromotion(initial) : emptyForm());
  const [imageUrl, setImageUrl] = useState(initial?.image_url ?? "");
  const [saving, setSaving] = useState(false);
  const [uploading, setUploading] = useState(false);
  const [uploadError, setUploadError] = useState("");

  useEffect(() => {
    setForm(initial ? formFromPromotion(initial) : emptyForm());
    setImageUrl(initial?.image_url ?? "");
  }, [initial?.id, initial?.image_url]);

  const toggleUnit = (unitId: string) => {
    setForm((prev) => ({
      ...prev,
      unit_ids: prev.unit_ids.includes(unitId)
        ? prev.unit_ids.filter((id) => id !== unitId)
        : [...prev.unit_ids, unitId],
    }));
  };

  const handleImage = async (e: ChangeEvent<HTMLInputElement>) => {
    const file = e.target.files?.[0];
    e.target.value = "";
    if (!file || !onUploadImage) return;
    if (file.size > 15 * 1024 * 1024) {
      setUploadError("Arquivo muito grande (máximo 15 MB)");
      return;
    }
    setUploadError("");
    setUploading(true);
    try {
      const newUrl = await onUploadImage(file);
      if (newUrl) setImageUrl(newUrl);
    } catch (err) {
      setUploadError(err instanceof Error ? err.message : "Erro ao enviar banner");
    } finally {
      setUploading(false);
    }
  };

  const submit = async (e: FormEvent) => {
    e.preventDefault();
    if (!form.title.trim() || !form.message.trim()) return;
    setSaving(true);
    try {
      await onSubmit(form);
    } finally {
      setSaving(false);
    }
  };

  return (
    <form onSubmit={submit} className="space-y-4 border border-white/10 bg-panel p-5">
      <div className="grid gap-3 lg:grid-cols-2">
        <label className="block text-sm">
          <span className="text-sand/60">Título (interno + IA)</span>
          <input
            className="mt-1 w-full rounded-md border border-white/15 bg-ink px-3 py-2"
            value={form.title}
            onChange={(e) => setForm({ ...form, title: e.target.value })}
            placeholder="Outubro Rosa — 50% na 1ª mensalidade"
            required
          />
        </label>
        <label className="block text-sm">
          <span className="text-sand/60">Público</span>
          <select
            className="mt-1 w-full rounded-md border border-white/15 bg-ink px-3 py-2"
            value={form.audience}
            onChange={(e) => setForm({ ...form, audience: e.target.value as Promotion["audience"] })}
          >
            {Object.entries(AUDIENCE_LABELS).map(([value, label]) => (
              <option key={value} value={value}>
                {label}
              </option>
            ))}
          </select>
        </label>
      </div>

      <label className="block text-sm">
        <span className="text-sand/60">Mensagem enviada ao cliente</span>
        <textarea
          className="mt-1 min-h-[140px] w-full rounded-md border border-white/15 bg-ink px-3 py-2"
          value={form.message}
          onChange={(e) => setForm({ ...form, message: e.target.value })}
          placeholder="Texto completo da promoção — a IA envia isso (e a imagem) automaticamente."
          required
        />
      </label>

      {initial && onUploadImage && (
        <div className="space-y-2">
          {imageUrl && (
            <img
              src={imageUrl}
              alt={form.title || "Banner da promoção"}
              className="max-h-48 rounded-md border border-white/10 object-contain"
            />
          )}
          <label className="inline-block cursor-pointer rounded-md border border-white/15 px-3 py-2 text-sm text-sand/70 hover:bg-white/5">
            {uploading ? "Enviando…" : imageUrl ? "Trocar banner" : "Enviar banner"}
            <input
              type="file"
              accept="image/jpeg,image/png,image/webp"
              className="hidden"
              onChange={handleImage}
              disabled={uploading}
            />
          </label>
          {uploadError && <p className="text-xs text-ember">{uploadError}</p>}
          {!imageUrl && !uploadError && (
            <p className="text-xs text-sand/45">
              JPEG, PNG ou WEBP — até 15 MB (comprimimos automaticamente). Salve a promoção antes de enviar o banner.
            </p>
          )}
        </div>
      )}

      <div className="grid gap-3 sm:grid-cols-2 lg:grid-cols-4">
        <label className="block text-sm">
          <span className="text-sand/60">Válida de</span>
          <input
            type="date"
            className="mt-1 w-full rounded-md border border-white/15 bg-ink px-3 py-2"
            value={form.valid_from}
            onChange={(e) => setForm({ ...form, valid_from: e.target.value })}
          />
        </label>
        <label className="block text-sm">
          <span className="text-sand/60">Válida até</span>
          <input
            type="date"
            className="mt-1 w-full rounded-md border border-white/15 bg-ink px-3 py-2"
            value={form.valid_until}
            onChange={(e) => setForm({ ...form, valid_until: e.target.value })}
          />
        </label>
        <label className="block text-sm">
          <span className="text-sand/60">Ordem</span>
          <input
            type="number"
            className="mt-1 w-full rounded-md border border-white/15 bg-ink px-3 py-2"
            value={form.sort_order}
            onChange={(e) => setForm({ ...form, sort_order: e.target.value })}
          />
        </label>
        <label className="flex items-center gap-2 pt-6 text-sm">
          <input
            type="checkbox"
            checked={form.is_active}
            onChange={(e) => setForm({ ...form, is_active: e.target.checked })}
          />
          Ativa
        </label>
      </div>

      <label className="block text-sm">
        <span className="text-sand/60">Palavras que disparam (opcional, separadas por vírgula)</span>
        <input
          className="mt-1 w-full rounded-md border border-white/15 bg-ink px-3 py-2"
          value={form.trigger_keywords}
          onChange={(e) => setForm({ ...form, trigger_keywords: e.target.value })}
          placeholder="outubro rosa, promoção, desconto"
        />
      </label>

      <div>
        <p className="text-sm text-sand/60">Unidades (vazio = todas)</p>
        <div className="mt-2 flex flex-wrap gap-2">
          {units.map((unit) => (
            <button
              key={unit.id}
              type="button"
              onClick={() => toggleUnit(unit.id)}
              className={`rounded-md border px-2 py-1 text-xs ${
                form.unit_ids.includes(unit.id)
                  ? "border-leaf bg-leaf/20 text-lime"
                  : "border-white/15 text-sand/60 hover:bg-white/5"
              }`}
            >
              {unit.name}
            </button>
          ))}
        </div>
      </div>

      <div className="grid gap-3 lg:grid-cols-2">
        <label className="flex items-center gap-2 text-sm">
          <input
            type="checkbox"
            checked={form.mention_on_plan_request}
            onChange={(e) => setForm({ ...form, mention_on_plan_request: e.target.checked })}
          />
          Mencionar automaticamente ao enviar planos
        </label>
        <label className="flex items-center gap-2 text-sm">
          <input
            type="checkbox"
            checked={form.requires_transfer}
            onChange={(e) => setForm({ ...form, requires_transfer: e.target.checked })}
          />
          Exige atendente (sem link de matrícula)
        </label>
      </div>

      {form.requires_transfer && (
        <label className="block text-sm">
          <span className="text-sand/60">Motivo da transferência</span>
          <input
            className="mt-1 w-full rounded-md border border-white/15 bg-ink px-3 py-2"
            value={form.transfer_reason}
            onChange={(e) => setForm({ ...form, transfer_reason: e.target.value })}
            placeholder="Interesse na promo Outubro Rosa — matrícula manual"
          />
        </label>
      )}

      <div className="flex gap-2">
        <button
          type="submit"
          disabled={saving}
          className="rounded-md bg-leaf px-4 py-2 font-semibold text-white hover:bg-lime disabled:opacity-50"
        >
          {saving ? "Salvando…" : submitLabel}
        </button>
        {onCancel && (
          <button
            type="button"
            onClick={onCancel}
            className="rounded-md border border-white/15 px-4 py-2 text-sand/60 hover:bg-white/5"
          >
            Cancelar
          </button>
        )}
      </div>
    </form>
  );
}

export default function PromotionsPage() {
  const { companyId } = useAuth();
  const [promotions, setPromotions] = useState<Promotion[]>([]);
  const [units, setUnits] = useState<Unit[]>([]);
  const [error, setError] = useState("");
  const [editingId, setEditingId] = useState<string | null>(null);
  const [creating, setCreating] = useState(false);

  const load = () => {
    if (!companyId) return;
    Promise.all([
      api<Promotion[]>("/promotions"),
      api<{ id: string; name: string; city: string }[]>("/units"),
    ])
      .then(([promos, unitList]) => {
        setPromotions(promos);
        setUnits(unitList.map((u) => ({ id: u.id, name: u.name, city: u.city })));
      })
      .catch((e) => setError(e instanceof Error ? e.message : "Erro ao carregar"));
  };

  useEffect(() => {
    load();
  }, [companyId]);

  const createPromotion = async (data: PromotionFormData) => {
    setError("");
    const created = await api<Promotion>("/promotions", {
      method: "POST",
      body: JSON.stringify(payloadFromForm(data)),
    });
    setCreating(false);
    setEditingId(created.id);
    setPromotions((prev) => [created, ...prev.filter((p) => p.id !== created.id)]);
  };

  const updatePromotion = async (id: string, data: PromotionFormData) => {
    setError("");
    await api(`/promotions/${id}`, { method: "PATCH", body: JSON.stringify(payloadFromForm(data)) });
    setEditingId(null);
    load();
  };

  const uploadImage = async (id: string, file: File): Promise<string> => {
    const updated = await apiUpload<Promotion>(`/promotions/${id}/image`, file);
    setPromotions((prev) => prev.map((p) => (p.id === id ? updated : p)));
    return updated.image_url ?? "";
  };

  const toggleActive = async (promo: Promotion) => {
    await api(`/promotions/${promo.id}`, {
      method: "PATCH",
      body: JSON.stringify({ is_active: !promo.is_active }),
    });
    load();
  };

  const deletePromotion = async (promo: Promotion) => {
    if (!confirm(`Excluir a promoção "${promo.title}"?`)) return;
    await api(`/promotions/${promo.id}`, { method: "DELETE" });
    load();
  };

  if (!companyId) {
    return <p className="text-sand/60">Selecione uma empresa.</p>;
  }

  return (
    <div className="space-y-8">
      <div>
        <h2 className="font-display text-3xl font-bold">Promoções</h2>
        <p className="mt-1 text-sand/55">
          Campanhas temporárias que a IA oferece ao falar de planos ou promoções — com imagem, público-alvo e
          transferência para atendente quando não há link.
        </p>
      </div>

      {error && <p className="text-ember">{error}</p>}

      {!creating && (
        <button
          type="button"
          onClick={() => {
            setCreating(true);
            setEditingId(null);
          }}
          className="rounded-md bg-leaf px-4 py-2 font-semibold text-white hover:bg-lime"
        >
          + Nova promoção
        </button>
      )}

      {creating && (
        <PromotionForm
          units={units}
          submitLabel="Criar promoção"
          onSubmit={createPromotion}
          onCancel={() => setCreating(false)}
        />
      )}

      <div className="space-y-4">
        {promotions.map((promo) =>
          editingId === promo.id ? (
            <PromotionForm
              key={promo.id}
              initial={promo}
              units={units}
              submitLabel="Salvar promoção"
              onSubmit={(data) => updatePromotion(promo.id, data)}
              onCancel={() => setEditingId(null)}
              onUploadImage={(file) => uploadImage(promo.id, file)}
            />
          ) : (
            <div key={promo.id} className="border border-white/10 bg-panel p-4">
              <div className="flex flex-wrap items-start justify-between gap-3">
                <div className="flex gap-3">
                  {promo.image_url && (
                    <img
                      src={promo.image_url}
                      alt={promo.title}
                      className="h-20 w-20 shrink-0 rounded-md border border-white/10 object-cover"
                    />
                  )}
                  <div>
                    <p className="font-display text-lg font-semibold">{promo.title}</p>
                    <p className="text-xs text-sand/50">
                      {AUDIENCE_LABELS[promo.audience]}
                      {promo.valid_from || promo.valid_until
                        ? ` · ${promo.valid_from ?? "…"} → ${promo.valid_until ?? "…"}`
                        : " · Sem prazo"}
                      {promo.mention_on_plan_request ? " · Após planos" : ""}
                      {promo.requires_transfer ? " · Transfere p/ atendente" : ""}
                    </p>
                    <p className="mt-2 line-clamp-3 whitespace-pre-wrap text-sm text-sand/75">{promo.message}</p>
                  </div>
                </div>
                <div className="flex shrink-0 flex-wrap gap-2">
                  <button
                    type="button"
                    onClick={() => {
                      setEditingId(promo.id);
                      setCreating(false);
                    }}
                    className="rounded-md border border-white/15 px-2 py-1 text-xs text-sand/70 hover:bg-white/5"
                  >
                    Editar
                  </button>
                  <button
                    type="button"
                    onClick={() => toggleActive(promo)}
                    className={`rounded-md border px-2 py-1 text-xs ${
                      promo.is_active
                        ? "border-leaf/40 text-lime hover:bg-leaf/10"
                        : "border-white/15 text-sand/50"
                    }`}
                  >
                    {promo.is_active ? "Ativa" : "Inativa"}
                  </button>
                  <button
                    type="button"
                    onClick={() => deletePromotion(promo)}
                    className="rounded-md border border-ember/40 px-2 py-1 text-xs text-ember hover:bg-ember/10"
                  >
                    Excluir
                  </button>
                </div>
              </div>
            </div>
          ),
        )}
        {promotions.length === 0 && !creating && (
          <p className="text-center text-sand/45">Nenhuma promoção cadastrada ainda.</p>
        )}
      </div>
    </div>
  );
}
