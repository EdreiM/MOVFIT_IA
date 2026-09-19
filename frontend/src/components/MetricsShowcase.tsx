type ShowcaseSnippet = {
  actor: string;
  text: string;
};

export type ShowcaseExample = {
  modality: string;
  title: string;
  description: string;
  occurred_at: string;
  snippets: ShowcaseSnippet[];
};

type Props = {
  examples: ShowcaseExample[];
  aiName?: string;
};

export default function MetricsShowcase({ examples, aiName = "IA" }: Props) {
  if (examples.length === 0) {
    return (
      <p className="mt-4 text-sand/45">
        Assim que eu concluir atendimentos reais de planos, avaliação, CPF e outros assuntos,
        aparecem aqui exemplos de conversas — um de cada tipo de coisa que faço hoje.
      </p>
    );
  }

  return (
    <div className="mt-4 space-y-4">
      <p className="text-sm leading-relaxed text-sand/70">
        Olá! Sou a <span className="text-lime">{aiName}</span>. Abaixo estão{" "}
        <span className="text-sand">{examples.length}</span> tipos de atendimento que já realizo
        na prática — cada um com um trecho real de conversa (dados dos clientes protegidos).
      </p>
      <div className="grid gap-5 lg:grid-cols-2">
        {examples.map((example, index) => (
          <article
            key={example.modality}
            className="border border-white/10 bg-ink/40 px-5 py-5"
          >
            <div className="flex items-start gap-3">
              <span className="flex h-8 w-8 shrink-0 items-center justify-center rounded-full bg-leaf/15 font-display text-sm font-bold text-lime">
                {index + 1}
              </span>
              <div className="min-w-0 flex-1">
                <p className="font-display text-lg font-semibold text-sand">{example.title}</p>
                <p className="mt-2 text-sm leading-relaxed text-sand/75">{example.description}</p>
              </div>
            </div>

            <div className="mt-4 border-t border-white/10 pt-4">
              <div className="mb-3 flex flex-wrap items-center justify-between gap-2">
                <p className="text-[10px] uppercase tracking-wider text-muted">Exemplo real</p>
                <p className="text-xs text-sand/40">
                  {new Date(example.occurred_at).toLocaleDateString("pt-BR", {
                    day: "2-digit",
                    month: "long",
                    year: "numeric",
                  })}
                </p>
              </div>
              <div className="space-y-2">
                {example.snippets.map((snippet, snippetIndex) => (
                  <div
                    key={snippetIndex}
                    className={`rounded-lg px-3 py-2.5 text-sm leading-relaxed ${
                      snippet.actor === "customer"
                        ? "mr-6 bg-white/5 text-sand/85"
                        : "ml-6 border border-leaf/15 bg-leaf/5 text-sand"
                    }`}
                  >
                    <p className="mb-1 text-[10px] font-medium text-sand/50">
                      {snippet.actor === "customer" ? "Cliente" : aiName}
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
