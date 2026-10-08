// Cor da taxa de sucesso de uma ferramenta — "text-lime" é vermelho nesse
// tema, então usar a cor de destaque fazia até 100% parecer falha.
export function successRateClass(rate: number | null): string {
  if (rate == null) return "text-sand/40";
  if (rate >= 0.9) return "text-emerald-400";
  if (rate >= 0.7) return "text-amber-400";
  return "text-ember";
}
