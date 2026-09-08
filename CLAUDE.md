# Mônica AI — convenções do projeto

Backend FastAPI + SQLAlchemy 2 (async) + Alembic, frontend React + Vite + Tailwind, Postgres, orquestração via n8n (webhooks). Multi-tenant: `Company` → `AiConfig` (padrão por empresa + opcional por integração) → `Conversation` → `Message`; `Lead` é o cadastro estruturado do cliente, separado do histórico de mensagens.

## Jobs em background

O projeto **assume um único worker/processo do backend rodando por vez** (confirmado no `docker-compose.yml`/`docker-compose.prod.yml` — sem múltiplas réplicas, sem Celery/Redis/APScheduler). Duas tarefas em background já existem sob essa premissa:

- `app/services/debounce.py` — timer em memória por conversa (agrupa rajadas de mensagem antes de responder).
- `app/services/followup.py` — varredura periódica (a cada 5min) de conversas inativas.

**Toda tarefa em background nova que executa trabalho (não só lê dados) deve usar `app/services/locks.py` (`advisory_lock`)** antes de agir, mesmo que hoje só exista um worker — é barato (uma trava consultiva do Postgres, `pg_advisory_lock`) e evita duplicar efeito colateral (mensagem repetida pro cliente, ferramenta chamada duas vezes) no dia em que o backend escalar pra múltiplas cópias. Padrão:

```python
async with advisory_lock(db, LOCK_NAMESPACE_X, key) as acquired:
    if not acquired:
        return  # outro processo já está fazendo isso agora
    ...trabalho...
```

Cada família de lock novo ganha um namespace inteiro sequencial em `locks.py` (não reaproveita namespace de outro job). Se o lock for por entidade (ex: por conversa), usa `uuid_lock_key(entidade.id)` como `key`; se for uma tarefa única/global (ex: uma varredura), usa `key=0` (padrão).

`pg_advisory_lock`/`pg_advisory_unlock` têm efeito imediato, fora da transação — por isso `advisory_lock` nunca faz commit/rollback por conta própria; quem decide o destino da transação é sempre o chamador.

## Confiabilidade de comportamento da IA

Testado repetidamente nesta sessão: quando a IA precisa **fazer algo de forma confiável como efeito colateral** de uma conversa (chamar uma ferramenta específica em vez de transferir, persistir um dado que apareceu de outro jeito, não prometer uma ação sem executá-la), **uma instrução de prompt sozinha não é suficiente** — funciona na maioria das vezes, mas falha o bastante em produção pra não poder depender só dela. O padrão estabelecido é sempre um **reforço duplo**: a instrução no prompt/descrição da ferramenta (ajuda o modelo a decidir certo) **+** uma trava determinística no código que garante o resultado independente do que o modelo decidiu (ver `_promised_transfer_without_acting`, `_LEAD_ARG_KEYS`/captura automática de CPF/unidade, `_wants_plan_info` em `message_flow.py`).

## Contrato de webhook (ferramentas e RAG via n8n)

Toda ferramenta/RAG customizada em n8n deve responder no formato `{"sucesso": bool, "mensagem": str, "dados": {...}}` (booleanos JSON literais, não string). Ver `n8n/TOOLS-CONTRATO.md` e `n8n/RAG-CONTRATO.md`. Campos `nome`/`cpf`/`email`/`data_nascimento` dentro de `dados` (ou na raiz) são capturados automaticamente pro cadastro do cliente — não precisa de nenhuma configuração extra pra isso funcionar.

## Testando mudanças no backend

Container dev é `movfitia-backend-1`, com `./backend:/app` montado (edições no host refletem direto, `--reload` já pega). Fluxo padrão: editar → `docker exec movfitia-backend-1 python -m pytest -q` → testar manualmente (Chat de teste, ou um script Python pontual via `docker exec` pra casos que não têm teste automatizado) → commit só depois de verificado.
