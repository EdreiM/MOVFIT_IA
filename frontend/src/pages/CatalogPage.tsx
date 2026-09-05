import { ChangeEvent, FormEvent, useEffect, useState } from "react";
import { api, apiUpload } from "../api";
import { useAuth } from "../auth";

type Plan = {
  id: string;
  unit_id: string;
  name: string;
  monthly_price: number;
  enrollment_fee: number;
  fidelity_months: number | null;
  payment_info: string | null;
  benefits: string[];
  image_url: string | null;
  signup_url: string | null;
  is_active: boolean;
};

type Unit = {
  id: string;
  company_id: string;
  name: string;
  city: string;
  unit_type: string | null;
  is_active: boolean;
  plans: Plan[];
};

type UnitFormData = { name: string; city: string; unit_type: string };
type PlanFormData = Omit<Plan, "id" | "unit_id" | "is_active">;

const currency = (v: number) =>
  v.toLocaleString("pt-BR", { style: "currency", currency: "BRL" });

function UnitForm({
  initial,
  onSubmit,
  onCancel,
  submitLabel,
}: {
  initial?: Unit;
  onSubmit: (data: UnitFormData) => void;
  onCancel?: () => void;
  submitLabel: string;
}) {
  const [name, setName] = useState(initial?.name ?? "");
  const [city, setCity] = useState(initial?.city ?? "");
  const [unitType, setUnitType] = useState(initial?.unit_type ?? "");

  const submit = (e: FormEvent) => {
    e.preventDefault();
    if (!name.trim() || !city.trim()) return;
    onSubmit({ name: name.trim(), city: city.trim(), unit_type: unitType.trim() });
    if (!initial) {
      setName("");
      setCity("");
      setUnitType("");
    }
  };

  return (
    <form onSubmit={submit} className="grid gap-3 border border-white/10 bg-panel p-5 sm:grid-cols-4">
      <input
        className="rounded-md border border-white/15 bg-ink px-3 py-2"
        placeholder="Nome da unidade (ex: Santarém 24h)"
        value={name}
        onChange={(e) => setName(e.target.value)}
        required
      />
      <input
        className="rounded-md border border-white/15 bg-ink px-3 py-2"
        placeholder="Cidade"
        value={city}
        onChange={(e) => setCity(e.target.value)}
        required
      />
      <input
        className="rounded-md border border-white/15 bg-ink px-3 py-2"
        placeholder="Tipo (Premium, Prime, Express...)"
        value={unitType}
        onChange={(e) => setUnitType(e.target.value)}
      />
      <div className="flex gap-2">
        <button type="submit" className="rounded-md bg-leaf px-4 py-2 font-semibold text-white hover:bg-lime">
          {submitLabel}
        </button>
        {onCancel && (
          <button
            type="button"
            onClick={onCancel}
            className="rounded-md border border-white/15 px-3 py-2 text-sand/60 hover:bg-white/5"
          >
            Cancelar
          </button>
        )}
      </div>
    </form>
  );
}

function PlanForm({
  initial,
  onSubmit,
  onCancel,
  onUploadImage,
  submitLabel,
  triggerLabel,
}: {
  initial?: Plan;
  onSubmit: (data: PlanFormData) => void;
  onCancel?: () => void;
  onUploadImage?: (file: File) => Promise<string>;
  submitLabel: string;
  triggerLabel?: string;
}) {
  const [name, setName] = useState(initial?.name ?? "");
  const [price, setPrice] = useState(initial ? String(initial.monthly_price) : "");
  const [fee, setFee] = useState(initial ? String(initial.enrollment_fee) : "0");
  const [fidelity, setFidelity] = useState(initial?.fidelity_months ? String(initial.fidelity_months) : "");
  const [paymentInfo, setPaymentInfo] = useState(initial?.payment_info ?? "");
  const [benefitsText, setBenefitsText] = useState(initial?.benefits.join("\n") ?? "");
  const [imageUrl, setImageUrl] = useState(initial?.image_url ?? "");
  const [signupUrl, setSignupUrl] = useState(initial?.signup_url ?? "");
  const [open, setOpen] = useState(!!initial);
  const [uploading, setUploading] = useState(false);
  const [uploadError, setUploadError] = useState("");

  const handleFileChange = async (e: ChangeEvent<HTMLInputElement>) => {
    const file = e.target.files?.[0];
    e.target.value = "";
    if (!file || !onUploadImage) return;
    setUploadError("");
    setUploading(true);
    try {
      const newUrl = await onUploadImage(file);
      setImageUrl(newUrl);
    } catch (err) {
      setUploadError(err instanceof Error ? err.message : "Erro ao enviar imagem");
    } finally {
      setUploading(false);
    }
  };

  const submit = (e: FormEvent) => {
    e.preventDefault();
    if (!name.trim() || !price) return;
    onSubmit({
      name: name.trim(),
      monthly_price: Number(price),
      enrollment_fee: Number(fee || 0),
      fidelity_months: fidelity ? Number(fidelity) : null,
      payment_info: paymentInfo.trim() || null,
      benefits: benefitsText
        .split("\n")
        .map((b) => b.trim())
        .filter(Boolean),
      image_url: imageUrl.trim() || null,
      signup_url: signupUrl.trim() || null,
    });
    if (!initial) {
      setName("");
      setPrice("");
      setFee("0");
      setFidelity("");
      setPaymentInfo("");
      setBenefitsText("");
      setImageUrl("");
      setSignupUrl("");
      setOpen(false);
    }
  };

  if (!open) {
    return (
      <button
        type="button"
        onClick={() => setOpen(true)}
        className="rounded-md border border-leaf px-3 py-1.5 text-sm text-lime hover:bg-leaf/10"
      >
        {triggerLabel ?? "+ Plano"}
      </button>
    );
  }

  return (
    <form onSubmit={submit} className="grid gap-2 border border-white/10 bg-ink/60 p-3 sm:grid-cols-2">
      <input
        className="rounded-md border border-white/15 bg-ink px-2 py-1.5 text-sm"
        placeholder="Nome do plano (ex: Mensal Recorrente)"
        value={name}
        onChange={(e) => setName(e.target.value)}
        required
      />
      <input
        className="rounded-md border border-white/15 bg-ink px-2 py-1.5 text-sm"
        placeholder="Valor mensal (ex: 197.00)"
        type="number"
        step="0.01"
        value={price}
        onChange={(e) => setPrice(e.target.value)}
        required
      />
      <input
        className="rounded-md border border-white/15 bg-ink px-2 py-1.5 text-sm"
        placeholder="Taxa de matrícula"
        type="number"
        step="0.01"
        value={fee}
        onChange={(e) => setFee(e.target.value)}
      />
      <input
        className="rounded-md border border-white/15 bg-ink px-2 py-1.5 text-sm"
        placeholder="Fidelidade (meses)"
        type="number"
        value={fidelity}
        onChange={(e) => setFidelity(e.target.value)}
      />
      <input
        className="rounded-md border border-white/15 bg-ink px-2 py-1.5 text-sm sm:col-span-2"
        placeholder="Forma de pagamento (ex: 12x no cartão de crédito)"
        value={paymentInfo}
        onChange={(e) => setPaymentInfo(e.target.value)}
      />
      <textarea
        className="min-h-16 rounded-md border border-white/15 bg-ink px-2 py-1.5 text-sm sm:col-span-2"
        placeholder={"Benefícios, um por linha"}
        value={benefitsText}
        onChange={(e) => setBenefitsText(e.target.value)}
      />
      <div className="space-y-2 sm:col-span-2">
        <div className="flex items-center gap-3">
          <input
            className="flex-1 rounded-md border border-white/15 bg-ink px-2 py-1.5 text-sm"
            placeholder="URL da imagem (opcional)"
            value={imageUrl}
            onChange={(e) => setImageUrl(e.target.value)}
          />
          {onUploadImage ? (
            <label className="shrink-0 cursor-pointer rounded-md border border-white/15 px-3 py-1.5 text-xs text-sand/70 hover:bg-white/5">
              {uploading ? "Enviando…" : "Enviar foto"}
              <input type="file" accept="image/jpeg,image/png,image/webp" className="hidden" onChange={handleFileChange} disabled={uploading} />
            </label>
          ) : (
            <span className="shrink-0 text-xs text-sand/40">salve o plano pra enviar foto</span>
          )}
        </div>
        {imageUrl && (
          <img src={imageUrl} alt="" className="h-24 w-24 rounded-md border border-white/10 object-cover" />
        )}
        {uploadError && <p className="text-xs text-ember">{uploadError}</p>}
      </div>
      <input
        className="rounded-md border border-white/15 bg-ink px-2 py-1.5 text-sm sm:col-span-2"
        placeholder="Link de cadastro/matrícula (a IA envia quando o cliente escolher este plano)"
        value={signupUrl}
        onChange={(e) => setSignupUrl(e.target.value)}
      />
      <div className="flex gap-2 sm:col-span-2">
        <button type="submit" className="rounded-md bg-leaf px-3 py-1.5 text-sm font-semibold text-white hover:bg-lime">
          {submitLabel}
        </button>
        <button
          type="button"
          onClick={() => {
            if (initial && onCancel) onCancel();
            else setOpen(false);
          }}
          className="rounded-md border border-white/15 px-3 py-1.5 text-sm text-sand/60 hover:bg-white/5"
        >
          Cancelar
        </button>
      </div>
    </form>
  );
}

export default function CatalogPage() {
  const { companyId } = useAuth();
  const [units, setUnits] = useState<Unit[]>([]);
  const [error, setError] = useState("");
  const [editingUnitId, setEditingUnitId] = useState<string | null>(null);
  const [editingPlanId, setEditingPlanId] = useState<string | null>(null);

  const load = () => {
    if (!companyId) return;
    api<Unit[]>(`/units`)
      .then(setUnits)
      .catch((e) => setError(e.message));
  };

  useEffect(() => {
    load();
  }, [companyId]);

  const createUnit = async (data: UnitFormData) => {
    setError("");
    try {
      await api(`/units`, {
        method: "POST",
        body: JSON.stringify({ ...data, unit_type: data.unit_type || null }),
      });
      await load();
    } catch (err) {
      setError(err instanceof Error ? err.message : "Erro ao criar unidade");
    }
  };

  const updateUnit = async (unitId: string, data: UnitFormData) => {
    setError("");
    try {
      await api(`/units/${unitId}`, {
        method: "PATCH",
        body: JSON.stringify({ ...data, unit_type: data.unit_type || null }),
      });
      setEditingUnitId(null);
      await load();
    } catch (err) {
      setError(err instanceof Error ? err.message : "Erro ao salvar unidade");
    }
  };

  const toggleUnitActive = async (unit: Unit) => {
    setError("");
    try {
      await api(`/units/${unit.id}`, {
        method: "PATCH",
        body: JSON.stringify({ is_active: !unit.is_active }),
      });
      await load();
    } catch (err) {
      setError(err instanceof Error ? err.message : "Erro ao atualizar");
    }
  };

  const deleteUnit = async (unit: Unit) => {
    if (!confirm(`Excluir a unidade "${unit.name}" e todos os planos dela?`)) return;
    setError("");
    try {
      await api(`/units/${unit.id}`, { method: "DELETE" });
      await load();
    } catch (err) {
      setError(err instanceof Error ? err.message : "Erro ao excluir");
    }
  };

  const createPlan = async (unitId: string, data: PlanFormData) => {
    setError("");
    try {
      await api(`/units/${unitId}/plans`, { method: "POST", body: JSON.stringify(data) });
      await load();
    } catch (err) {
      setError(err instanceof Error ? err.message : "Erro ao criar plano");
    }
  };

  const updatePlan = async (planId: string, data: PlanFormData) => {
    setError("");
    try {
      await api(`/plans/${planId}`, { method: "PATCH", body: JSON.stringify(data) });
      setEditingPlanId(null);
      await load();
    } catch (err) {
      setError(err instanceof Error ? err.message : "Erro ao salvar plano");
    }
  };

  const uploadPlanImage = async (planId: string, file: File): Promise<string> => {
    const updated = await apiUpload<Plan>(`/plans/${planId}/image`, file);
    await load();
    return updated.image_url ?? "";
  };

  const togglePlanActive = async (plan: Plan) => {
    setError("");
    try {
      await api(`/plans/${plan.id}`, {
        method: "PATCH",
        body: JSON.stringify({ is_active: !plan.is_active }),
      });
      await load();
    } catch (err) {
      setError(err instanceof Error ? err.message : "Erro ao atualizar");
    }
  };

  const deletePlan = async (plan: Plan) => {
    if (!confirm(`Excluir o plano "${plan.name}"?`)) return;
    setError("");
    try {
      await api(`/plans/${plan.id}`, { method: "DELETE" });
      await load();
    } catch (err) {
      setError(err instanceof Error ? err.message : "Erro ao excluir");
    }
  };

  if (!companyId) {
    return <p className="text-sand/60">Selecione uma empresa.</p>;
  }

  return (
    <div className="space-y-8">
      <div>
        <h2 className="font-display text-3xl font-bold">Unidades & Planos</h2>
        <p className="mt-1 text-sand/55">
          Fonte oficial que a IA usa pra responder sobre planos e preços — edite aqui, sem
          precisar mexer em RAG ou código. Atualiza na hora.
        </p>
      </div>

      <UnitForm onSubmit={createUnit} submitLabel="+ Nova unidade" />

      {error && <p className="text-ember">{error}</p>}

      <div className="space-y-6">
        {units.map((unit) => (
          <div key={unit.id} className="border border-white/10 bg-panel">
            {editingUnitId === unit.id ? (
              <div className="border-b border-white/10 p-4">
                <UnitForm
                  initial={unit}
                  submitLabel="Salvar unidade"
                  onSubmit={(data) => updateUnit(unit.id, data)}
                  onCancel={() => setEditingUnitId(null)}
                />
              </div>
            ) : (
              <div className="flex items-start justify-between gap-3 border-b border-white/10 p-4">
                <div>
                  <p className="font-display text-lg font-semibold">
                    {unit.name}{" "}
                    <span className="text-sm font-normal text-sand/50">
                      {unit.city}
                      {unit.unit_type ? ` · ${unit.unit_type}` : ""}
                    </span>
                  </p>
                </div>
                <div className="flex shrink-0 items-center gap-2">
                  <button
                    type="button"
                    onClick={() => setEditingUnitId(unit.id)}
                    className="rounded-md border border-white/15 px-2 py-1 text-xs text-sand/70 hover:bg-white/5"
                  >
                    Editar
                  </button>
                  <button
                    type="button"
                    onClick={() => toggleUnitActive(unit)}
                    className={`rounded-md border px-2 py-1 text-xs ${
                      unit.is_active
                        ? "border-leaf/40 text-lime hover:bg-leaf/10"
                        : "border-white/15 text-sand/50 hover:bg-white/5"
                    }`}
                  >
                    {unit.is_active ? "Ativa" : "Inativa"}
                  </button>
                  <button
                    type="button"
                    onClick={() => deleteUnit(unit)}
                    className="rounded-md border border-ember/40 px-2 py-1 text-xs text-ember hover:bg-ember/10"
                  >
                    Excluir
                  </button>
                </div>
              </div>
            )}

            <div className="space-y-3 p-4">
              {unit.plans.map((plan) =>
                editingPlanId === plan.id ? (
                  <PlanForm
                    key={plan.id}
                    initial={plan}
                    submitLabel="Salvar plano"
                    onSubmit={(data) => updatePlan(plan.id, data)}
                    onCancel={() => setEditingPlanId(null)}
                    onUploadImage={(file) => uploadPlanImage(plan.id, file)}
                  />
                ) : (
                  <div key={plan.id} className="flex items-start justify-between gap-3 border border-white/10 p-3">
                    <div className="flex items-start gap-3">
                      {plan.image_url && (
                        <img
                          src={plan.image_url}
                          alt={plan.name}
                          className="h-16 w-16 shrink-0 rounded-md border border-white/10 object-cover"
                        />
                      )}
                      <div className="space-y-1">
                        <p className="font-medium">
                          {plan.name}{" "}
                          <span className="text-sand/60">{currency(plan.monthly_price)}/mês</span>
                        </p>
                        <p className="text-xs text-sand/50">
                          {plan.enrollment_fee > 0 && <>Matrícula {currency(plan.enrollment_fee)} · </>}
                          {plan.fidelity_months && <>Fidelidade {plan.fidelity_months} meses · </>}
                          {plan.payment_info}
                        </p>
                        {plan.benefits.length > 0 && (
                          <p className="text-xs text-sand/40">{plan.benefits.join(" · ")}</p>
                        )}
                        {plan.signup_url ? (
                          <p className="break-all text-xs text-lime">🔗 {plan.signup_url}</p>
                        ) : (
                          <p className="text-xs text-ember/70">Sem link de cadastro cadastrado</p>
                        )}
                      </div>
                    </div>
                    <div className="flex shrink-0 items-center gap-2">
                      <button
                        type="button"
                        onClick={() => setEditingPlanId(plan.id)}
                        className="rounded-md border border-white/15 px-2 py-1 text-xs text-sand/70 hover:bg-white/5"
                      >
                        Editar
                      </button>
                      <button
                        type="button"
                        onClick={() => togglePlanActive(plan)}
                        className={`rounded-md border px-2 py-1 text-xs ${
                          plan.is_active
                            ? "border-leaf/40 text-lime hover:bg-leaf/10"
                            : "border-white/15 text-sand/50 hover:bg-white/5"
                        }`}
                      >
                        {plan.is_active ? "Ativo" : "Inativo"}
                      </button>
                      <button
                        type="button"
                        onClick={() => deletePlan(plan)}
                        className="rounded-md border border-ember/40 px-2 py-1 text-xs text-ember hover:bg-ember/10"
                      >
                        Excluir
                      </button>
                    </div>
                  </div>
                )
              )}
              {unit.plans.length === 0 && (
                <p className="text-sm text-sand/45">Nenhum plano cadastrado nesta unidade.</p>
              )}
              <PlanForm onSubmit={(data) => createPlan(unit.id, data)} submitLabel="Salvar plano" />
            </div>
          </div>
        ))}
        {units.length === 0 && (
          <p className="text-center text-sand/45">Nenhuma unidade cadastrada ainda.</p>
        )}
      </div>
    </div>
  );
}
