// Etiquetas automáticas dos clientes ("categoria:valor") e etapas do funil
// de vendas — os valores vêm de backend/app/services/lead_insights.py.

export const SALES_STAGE_LABELS: Record<string, string> = {
  interessado: "Interessado",
  qualificado: "Qualificado",
  proposta: "Proposta",
  matriculado: "Matriculado",
  perdido: "Perdido",
};

const CATEGORY_LABELS: Record<string, string> = {
  assunto: "Assunto",
  objecao: "Objeção",
  unidade: "Unidade",
  plano: "Plano",
  promo: "Promoção",
  perfil: "Perfil",
};

const VALUE_LABELS: Record<string, string> = {
  "assunto:planos": "planos",
  "assunto:promocao": "promoção",
  "assunto:avaliacao_fisica": "avaliação física",
  "assunto:financeiro": "financeiro/parcelas",
  "assunto:cancelamento": "cancelamento",
  "assunto:tour": "tour",
  "assunto:informacoes": "informações da unidade",
  "assunto:convidados": "convidados",
  "assunto:app": "aplicativo",
  "objecao:preco": "preço",
  "objecao:fidelidade": "fidelidade",
  "objecao:vou_pensar": "vai pensar",
  "objecao:tempo": "falta de tempo",
  "objecao:distancia": "distância",
  "perfil:aluno": "aluno",
  "perfil:nao_aluno": "não é aluno",
};

const CATEGORY_CLASSES: Record<string, string> = {
  assunto: "border-white/20 text-sand/75",
  objecao: "border-amber-400/40 text-amber-300",
  unidade: "border-sky-400/40 text-sky-300",
  plano: "border-emerald-400/40 text-emerald-300",
  promo: "border-pink-400/40 text-pink-300",
  perfil: "border-white/20 text-sand/55",
};

export function tagLabel(tag: string): string {
  const [category, ...rest] = tag.split(":");
  const value = VALUE_LABELS[tag] ?? rest.join(":");
  return `${CATEGORY_LABELS[category] ?? category}: ${value}`;
}

export function tagClass(tag: string): string {
  return CATEGORY_CLASSES[tag.split(":")[0]] ?? "border-white/20 text-sand/70";
}

export const LOST_REASON_LABELS: Record<string, string> = {
  sem_resposta: "parou de responder",
  preco: "preço",
  fidelidade: "fidelidade",
  vou_pensar: "ficou de pensar",
  tempo: "falta de tempo",
  distancia: "distância",
};
