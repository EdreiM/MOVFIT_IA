# Webhooks RAG (n8n)

A Mônica chama cada RAG com:

```json
{
  "pergunta": "texto da mensagem do cliente",
  "contexto": {
    "plano_em_negociacao": null
  }
}
```

Quando no futuro soubermos o plano em negociação, `plano_em_negociacao` vem preenchido (ex: `"Premium"`).

## Prompt no nó Vector Store (PGVector)

Use esta expressão (não concatena `" plano "` vazio):

```
={{ ($json.body?.pergunta || $json.pergunta || '') + (($json.body?.contexto?.plano_em_negociacao || $json.contexto?.plano_em_negociacao) ? (' | plano: ' + ($json.body?.contexto?.plano_em_negociacao || $json.contexto?.plano_em_negociacao)) : '') }}
```

## Resposta esperada (nó Organiza)

```json
{
  "encontrado": true,
  "resposta": "...",
  "chunks": [{ "titulo": "...", "conteudo": "..." }],
  "fontes": [],
  "confianca": 0
}
```

Paths atuais:
- Base: `POST .../webhook/RAG_MOVFIT`
- Planos: `POST .../webhook/RAG_MOVFIT_PLANOS`
