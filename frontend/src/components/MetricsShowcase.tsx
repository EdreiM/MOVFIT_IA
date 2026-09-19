type ShowcaseSnippet = {
  actor: string;
  text: string;
};

export type ShowcaseExample = {
  modality: string;
  title: string;
  outcome: string;
  evidence: string;
  occurred_at: string;
  snippets: ShowcaseSnippet[];
};

function actorLabel(actor: string) {
  return actor === "customer" ? "Cliente" : "IA";
}

export default function MetricsShowcase({ examples }: { examples: ShowcaseExample[] }) {
  if (examples.length === 0) {
    return (
      <p className="mt-4 text-sand/45">
        Ainda não há registros auditáveis suficientes. Quando a IA executar planos, avaliação,
        CPF etc. em conversas reais, um registro por modalidade aparece aqui automaticamente.
      </p>
    );
  }

  return (
    <div className="mt-4 space-y-4">
      <p className="text-sm text-sand/55">
        {examples.length} modalidade{examples.length === 1 ? "" : "s"} auditada
        {examples.length === 1 ? "" : "s"} · amostra mais recente de cada tipo, com evidência nos logs
      </p>
      <div className="grid gap-4 lg:grid-cols-2">
        {examples.map((record) => (
          <article
            key={record.modality}
            className="border border-white/10 bg-ink/40 px-4 py-4"
          >
            <div className="flex flex-wrap items-start justify-between gap-2">
              <div>
                <div className="flex flex-wrap items-center gap-2">
                  <p className="font-display text-lg font-semibold text-lime">{record.title}</p>
                  <span className="rounded border border-leaf/30 bg-leaf/10 px-2 py-0.5 text-[10px] uppercase tracking-wider text-lime">
                    Verificado
                  </span>
                </div>
                <p className="mt-2 text-sm text-sand/75">
                  <span className="text-muted">Resultado: </span>
                  {record.outcome}
                </p>
              </div>
              <p className="text-xs text-sand/40">
                {new Date(record.occurred_at).toLocaleString("pt-BR", {
                  day: "2-digit",
                  month: "2-digit",
                  year: "2-digit",
                  hour: "2-digit",
                  minute: "2-digit",
                })}
              </p>
            </div>
            <p className="mt-3 border-l-2 border-leaf/40 pl-3 text-xs text-sand/55">
              <span className="font-medium text-sand/70">Evidência · </span>
              {record.evidence}
            </p>
            <div className="mt-4">
              <p className="mb-2 text-[10px] uppercase tracking-wider text-muted">Trecho auditado (anonimizado)</p>
              <div className="space-y-2">
                {record.snippets.map((snippet, index) => (
                  <div
                    key={index}
                    className={`rounded-md px-3 py-2 text-sm leading-relaxed ${
                      snippet.actor === "customer"
                        ? "bg-white/5 text-sand/80"
                        : "border border-leaf/20 bg-leaf/5 text-sand"
                    }`}
                  >
                    <p className="mb-1 text-[10px] uppercase tracking-wider text-muted">
                      {actorLabel(snippet.actor)}
                    </p>
                    <p>{snippet.text}</p>
                  </div>
                ))}
              </div>
            </div>
          </article>
        ))}
      </div>
    </div>
  );
}
