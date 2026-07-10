from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.core.config import settings
from app.core.db import Base, engine
from app.routers import carteiras, health, posicoes, proventos, transacoes


@asynccontextmanager
async def lifespan(_: FastAPI):
    # Em desenvolvimento, cria as tabelas automaticamente. Em produção, use
    # migrations (Alembic) ou rode sql/schema.sql no Supabase.
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

app.include_router(health.router)
app.include_router(carteiras.router)
app.include_router(transacoes.router)
app.include_router(proventos.router)
app.include_router(posicoes.router)
