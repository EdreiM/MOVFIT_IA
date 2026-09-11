# Webhooks de Ferramentas (n8n)

Quando a IA decide usar uma ferramenta durante a conversa, a Mônica chama o webhook configurado com:

```json
{
  "ferramenta": "consultar_aluno",
  "argumentos": {
    "cpf": "12345678900"
  },
  "contexto": {
    "telefone_cliente": "5593999999999",
    "nome_cliente": "João",
    "conversation_id": "uuid-da-conversa"
  }
}
```

- `ferramenta`: a chave (`tool_key`) cadastrada na página **Ferramentas** do painel — use pra decidir qual fluxo do n8n rodar.
- `argumentos`: o que a IA extraiu da conversa, conforme os parâmetros cadastrados na ferramenta. **Não** cadastre telefone/número do cliente como parâmetro — ele já vem sempre em `contexto.telefone_cliente`, é mais confiável (não depende da IA "adivinhar" certo) e evita repetir a mesma informação de duas formas.
- `contexto.telefone_cliente`: sempre só dígitos (sem `+`, espaço ou traço), não importa qual plataforma originou a conversa — normalizado pelo backend antes de chamar o webhook, então todo fluxo n8n pode confiar nesse formato único. Use esse número pra mandar mensagem/mídia direto pro cliente (Evolution API, Chatwoot, etc.) quando a ferramenta precisar enviar algo — a Mônica não envia mídia por conta própria, quem faz isso é o próprio workflow.

## Resposta esperada

```json
{
  "sucesso": true,
  "mensagem": "Enviei as imagens dos planos pro cliente.",
  "dados": {}
}
```

- `sucesso`: `true`/`false` — se der `false`, a IA é avisada e trata o erro na resposta pro cliente (ex: "não consegui enviar agora, um instante").
- `mensagem`: texto opcional que ajuda a IA a confirmar/explicar o resultado pro cliente. Não precisa ser a resposta final — a IA usa isso como informação e escreve a mensagem com o próprio estilo dela.
- `dados`: qualquer informação útil que a ferramenta precise devolver (ex: nome do aluno, plano atual, vencimento) — a IA usa isso pra continuar a conversa.

## Ferramentas com efeito interno no painel

Quatro chaves são especiais — além de chamar o webhook, o backend da Mônica também faz algo a mais:

- `transferir_atendimento`: quando `sucesso: true`, desliga a IA da conversa e marca como "com atendente humano".
- `encerrar_atendimento`: quando `sucesso: true`, marca a conversa como resolvida.
- `verificar_unidade_por_cpf`: quando `sucesso: true` e `dados.unidade` vem preenchido, grava essa unidade no cadastro do cliente (Lead), marca como aluno (`is_student`) e passa a usar essa unidade nas próximas ferramentas de aluno — sem perguntar de novo. Resposta esperada:
  ```json
  {
    "sucesso": true,
    "mensagem": "Aluno encontrado na unidade Santarém - 24 horas.",
    "dados": { "cpf": "12345678900", "unidade": "Santarém - 24 horas" }
  }
  ```
  Use o nome **exato** da unidade como no catálogo do painel. Não use essa ferramenta pra quem só pede planos/preços (aí a IA pergunta de qual unidade o lead quer saber).
- `enviar_imagens_planos`: antes de chamar o webhook, o backend cruza o que a IA extraiu (nome da unidade, em qualquer parâmetro que você tenha configurado) com o catálogo de **Unidades & Planos** do painel, e manda as URLs de imagem reais em `contexto.imagens_planos`:
  ```json
  "imagens_planos": [
    { "unidade": "Santarém - 24 horas", "plano": "Plano Anual Parcelado", "url": "https://.../uploads/plans/xxx.png" },
    { "unidade": "Santarém - 24 horas", "plano": "Plano Mensal Recorrente", "url": "https://.../uploads/plans/yyy.png" }
  ]
  ```
  O casamento é pelo **nome exato da unidade** como está cadastrado no painel (ex: `Santarém - Nova República`, não só `Nova República`) — por isso a descrição do parâmetro "Unidade" deve instruir a IA a reproduzir o nome tal como aparece no catálogo. Cidade sozinha só é usada como fallback, e apenas quando existe uma única unidade ativa naquela cidade (evita mandar a unidade errada quando duas dividem a mesma cidade).

  Se a unidade tiver mais de um plano com imagem (ex: mensal e anual), cadastre também um parâmetro de **nome do plano** — quando a IA mandar um valor que bate com o nome de um plano daquela unidade, só a imagem desse plano entra na lista; se nenhum parâmetro bater com nome de plano, vêm todas as imagens ativas da unidade (comportamento anterior, mantido pra ferramentas sem esse parâmetro).

  Se vier vazia, é porque a unidade não bateu com nenhuma cadastrada ou nenhum plano dela (ou do plano pedido) tem imagem ainda.

Qualquer outra `tool_key` (consultar aluno, ou o que for cadastrado) é tratada de forma genérica: o webhook faz o trabalho de verdade, a Mônica só repassa o resultado.

## Cadastro no painel

Em **Ferramentas**, pra cada uma: nome, tipo (as duas especiais acima, ou "Personalizada" com uma chave livre), descrição (é o que a IA lê pra saber quando chamar essa ferramenta — capriche), URL do webhook, e os parâmetros que a IA deve extrair da conversa (nome, tipo, descrição, se é obrigatório).
