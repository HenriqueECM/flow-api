from fastapi import APIRouter, Depends, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.db import get_db
from app.deps import get_owned_carteira
from app.models import Carteira, Provento
from app.schemas import ProventoCreate, ProventoOut

router = APIRouter(prefix="/carteiras/{carteira_id}/proventos", tags=["proventos"])


@router.get("", response_model=list[ProventoOut])
async def list_proventos(
    carteira: Carteira = Depends(get_owned_carteira),
    db: AsyncSession = Depends(get_db),
) -> list[Provento]:
    result = await db.execute(
        select(Provento)
        .where(Provento.carteira_id == carteira.id)
        .order_by(Provento.data_pagamento.desc().nullslast(), Provento.created_at.desc())
    )
    return list(result.scalars().all())


@router.post("", response_model=ProventoOut, status_code=status.HTTP_201_CREATED)
async def create_provento(
    payload: ProventoCreate,
    carteira: Carteira = Depends(get_owned_carteira),
    db: AsyncSession = Depends(get_db),
) -> Provento:
    provento = Provento(carteira_id=carteira.id, **payload.model_dump())
    db.add(provento)
    await db.commit()
    await db.refresh(provento)
    return provento
