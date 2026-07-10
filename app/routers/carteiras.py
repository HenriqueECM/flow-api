from fastapi import APIRouter, Depends, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.db import get_db
from app.core.security import CurrentUser, get_current_user
from app.deps import get_owned_carteira
from app.models import Carteira
from app.schemas import CarteiraCreate, CarteiraOut

router = APIRouter(prefix="/carteiras", tags=["carteiras"])


@router.get("", response_model=list[CarteiraOut])
async def list_carteiras(
    user: CurrentUser = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> list[Carteira]:
    result = await db.execute(
        select(Carteira)
        .where(Carteira.user_id == user.id)
        .order_by(Carteira.created_at)
    )
    return list(result.scalars().all())


@router.post("", response_model=CarteiraOut, status_code=status.HTTP_201_CREATED)
async def create_carteira(
    payload: CarteiraCreate,
    user: CurrentUser = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> Carteira:
    carteira = Carteira(user_id=user.id, nome=payload.nome)
    db.add(carteira)
    await db.commit()
    await db.refresh(carteira)
    return carteira


@router.get("/ativa", response_model=CarteiraOut)
async def get_carteira_ativa(
    user: CurrentUser = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> Carteira:
    """Garante que o usuário tenha ao menos uma carteira e retorna a primeira.

    Se ainda não existir nenhuma, cria a carteira padrão "Minha Carteira".
    (Seleção de múltiplas carteiras fica para depois — por ora, carteira única.)
    """
    result = await db.execute(
        select(Carteira)
        .where(Carteira.user_id == user.id)
        .order_by(Carteira.created_at)
        .limit(1)
    )
    carteira = result.scalars().first()

    if carteira is None:
        carteira = Carteira(user_id=user.id, nome="Minha Carteira")
        db.add(carteira)
        await db.commit()
        await db.refresh(carteira)

    return carteira


@router.get("/{carteira_id}", response_model=CarteiraOut)
async def get_carteira(carteira: Carteira = Depends(get_owned_carteira)) -> Carteira:
    return carteira


@router.delete("/{carteira_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_carteira(
    carteira: Carteira = Depends(get_owned_carteira),
    db: AsyncSession = Depends(get_db),
) -> None:
    await db.delete(carteira)
    await db.commit()
