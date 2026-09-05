# Mônica AI

IA de atendimento (SDR/CS) multi-tenant — MVP inicial voltado para a Mov Fit.

## Stack

- **Backend**: Python + FastAPI + SQLAlchemy 2 (async) + Alembic
- **Frontend**: React + Vite + TailwindCSS
- **Banco**: PostgreSQL 16
- **Orquestração**: n8n (RAGs/tools via webhook) — integração na Fase 2+

## Subir localmente

```bash
docker compose up --build
```

- API: http://localhost:8000  
- Docs (Swagger): http://localhost:8000/docs  
- Dashboard: http://localhost:5174  

### Credenciais seed (dev)

- E-mail: `admin@movfit.com`
- Senha: `admin123`
- Papel: `super_admin` (empresa Mov Fit)

## Estrutura

```
/backend   → API FastAPI
/frontend  → Dashboard React
```

## Testes (backend)

Roda contra um banco `monica_test` separado (mesma instância Postgres), recriado do zero a cada execução — não toca no banco de dev.

```bash
docker exec movfitia-backend-1 pip install -r requirements-dev.txt
docker exec movfitia-backend-1 python -m pytest tests/ -v
```

## Variáveis de ambiente (backend)

Copie `backend/.env.example` para `backend/.env` e ajuste se necessário.

## MVP (Fase 1)

- Auth JWT (login / refresh)
- Empresas, usuários, números/canais
- Conversas e mensagens
- Configuração da IA (prompt, modelo OpenAI, API key, RAG)
- Webhook inbound genérico + adaptador `movfit_hub_v1`
- Dashboard: login, métricas, números, config da IA, conversas
