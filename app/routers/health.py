"""Checagens de saúde.

Duas, com públicos diferentes:

- `/health` (liveness) — "o processo está de pé?". É o que a plataforma usa para
  decidir se **reinicia** o container. Não toca no banco de propósito: reiniciar
  a API não conserta um Postgres fora do ar, e amarrar um ao outro faz uma
  oscilação de rede do Supabase virar reinicialização em laço.

- `/health/ready` (readiness) — "dá para atender requisições?". Verifica o banco
  e responde 503 quando ele não responde. Serve para monitoramento e diagnóstico:
  informa sem provocar reinício.
"""

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.db import get_db

router = APIRouter(tags=["health"])


@router.get("/health")
async def health() -> dict[str, str]:
    """Liveness: responde enquanto o processo estiver vivo."""
    return {"status": "ok"}


@router.get("/health/ready")
async def health_ready(db: AsyncSession = Depends(get_db)) -> dict[str, str]:
    """Readiness: 200 se o banco responde, 503 se não."""
    try:
        await db.execute(text("SELECT 1"))
    except Exception:
        # `except Exception`, e não `except SQLAlchemyError`: banco fora do ar
        # levanta ConnectionRefusedError, que é um OSError puro e escaparia de um
        # except mais estreito — justamente o caso mais comum. DNS falhando dá
        # gaierror; timeout dá TimeoutError. Para uma sonda de readiness, toda
        # falha tem a mesma resposta ("não estou pronto"), e deixar uma escapar
        # produziria 500 — que diz "eu quebrei" em vez de "o banco não responde".
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Banco de dados indisponível.",
        )
    return {"status": "ok"}
