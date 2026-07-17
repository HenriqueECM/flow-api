from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.core.config import settings
from app.core.db import Base, engine
from app.core.observability import (
    RequestIdMiddleware,
    init_observability,
    unhandled_exception_handler,
)
from app.routers import (
    carteiras,
    health,
    importacao,
    posicoes,
    proventos,
    relatorios,
    transacoes,
)


@asynccontextmanager
async def lifespan(_: FastAPI):
    # Logging estruturado e Sentry (stub) antes de qualquer trabalho, para que os
    # logs do startup em diante já saiam em JSON.
    init_observability()
    # Em desenvolvimento, cria as tabelas automaticamente. Em produção, use
    # migrations (Alembic).
    if settings.dev_create_tables:
        async with engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)
    yield


app = FastAPI(title="Flow API", version="0.1.0", lifespan=lifespan)

app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.cors_origins_list,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Por último = mais externo: envolve CORS, autenticação, routers e erros, então
# todo request ganha request_id e access log (add_middleware empilha ao contrário).
app.add_middleware(RequestIdMiddleware)

# Toda exceção não tratada vira um 500 padronizado, logada com request_id.
app.add_exception_handler(Exception, unhandled_exception_handler)

app.include_router(health.router)
app.include_router(carteiras.router)
app.include_router(transacoes.router)
app.include_router(proventos.router)
app.include_router(posicoes.router)
app.include_router(importacao.router)
app.include_router(relatorios.router)
