import asyncio
import logging
import os
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles

from app.config import get_settings
from app.services.followup import periodic_followup_loop

# Sem isso, todo logger.info(...) do app (ex: diagnóstico de execução de
# ferramentas) é descartado silenciosamente — o nível padrão do root logger
# é WARNING.
logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
from app.routers import (
    admin,
    ai_configs,
    api_keys,
    auth,
    catalog,
    companies,
    conversations,
    integrations,
    leads,
    metrics,
    numbers,
    public_api,
)

settings = get_settings()


@asynccontextmanager
async def lifespan(_app: FastAPI):
    # Varredura periódica de follow-up (in-process, sem broker externo —
    # mesma filosofia do debounce.py). Assume um único worker uvicorn, como
    # já é o caso hoje (ver Dockerfile/docker-compose); com múltiplos
    # workers/réplicas isso duplicaria as varreduras.
    followup_task = asyncio.create_task(periodic_followup_loop())
    yield
    followup_task.cancel()


app = FastAPI(
    title=settings.app_name,
    version="0.1.0",
    description="Mônica AI — API multi-tenant de atendimento",
    lifespan=lifespan,
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.cors_origins_list,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

UPLOADS_DIR = os.path.join(os.path.dirname(__file__), "..", "uploads")
os.makedirs(os.path.join(UPLOADS_DIR, "plans"), exist_ok=True)
app.mount("/uploads", StaticFiles(directory=UPLOADS_DIR), name="uploads")

app.include_router(auth.router)
app.include_router(companies.router)
app.include_router(numbers.router)
app.include_router(conversations.router)
app.include_router(ai_configs.router)
app.include_router(catalog.router)
app.include_router(catalog.plans_router)
app.include_router(integrations.router)
app.include_router(leads.router)
app.include_router(metrics.router)
app.include_router(admin.router)
app.include_router(api_keys.router)
app.include_router(public_api.router)


@app.get("/health")
async def root_health():
    return {"status": "ok", "app": settings.app_name}
