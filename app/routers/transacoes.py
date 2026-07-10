from fastapi import APIRouter, Depends, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.db import get_db
from app.deps import get_owned_carteira
from app.models import Carteira, Transacao
from app.schemas import TransacaoCreate, TransacaoOut

router = APIRouter(prefix="/carteiras/{carteira_id}/transacoes", tags=["transacoes"])


@router.get("", response_model=list[TransacaoOut])
async def list_transacoes(
    carteira: Carteira = Depends(get_owned_carteira),
    db: AsyncSession = Depends(get_db),
) -> list[Transacao]:
    result = await db.execute(
        select(Transacao)
        .where(Transacao.carteira_id == carteira.id)
        .order_by(Transacao.data.desc(), Transacao.created_at.desc())
    )
    return list(result.scalars().all())


@router.post("", response_model=TransacaoOut, status_code=status.HTTP_201_CREATED)
async def create_transacao(
    payload: TransacaoCreate,
    carteira: Carteira = Depends(get_owned_carteira),
    db: AsyncSession = Depends(get_db),
) -> Transacao:
    transacao = Transacao(carteira_id=carteira.id, **payload.model_dump())
    db.add(transacao)
    await db.commit()
    await db.refresh(transacao)
    return transacao
