from uuid import UUID

from fastapi import APIRouter, Depends, status
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
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


async def _carteira_padrao(db: AsyncSession, user_id: UUID) -> Carteira | None:
    return await db.scalar(
        select(Carteira).where(
            Carteira.user_id == user_id, Carteira.is_default.is_(True)
        )
    )


@router.get("/ativa", response_model=CarteiraOut)
async def get_carteira_ativa(
    user: CurrentUser = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> Carteira:
    """Garante que o usuário tenha uma carteira padrão e a devolve.

    Único endpoint de leitura que escreve: se o usuário ainda não tem carteira,
    cria a padrão "Minha Carteira".
    """
    padrao = await _carteira_padrao(db, user.id)
    if padrao is not None:
        return padrao

    # Tem carteira, nenhuma marcada como padrão. Acontece com quem criou a
    # primeira via POST /carteiras (que não marca) e com qualquer linha que
    # escape da migração. Promove a mais antiga — o mesmo critério que este
    # endpoint usava antes do is_default existir, então nada muda para o
    # usuário. Sem corrida: duas requisições escolheriam a mesma linha.
    mais_antiga = await db.scalar(
        select(Carteira)
        .where(Carteira.user_id == user.id)
        .order_by(Carteira.created_at, Carteira.id)
        .limit(1)
    )
    if mais_antiga is not None:
        mais_antiga.is_default = True
        await db.commit()
        return mais_antiga

    carteira = Carteira(user_id=user.id, nome="Minha Carteira", is_default=True)
    db.add(carteira)
    try:
        await db.commit()
    except IntegrityError:
        # Outra requisição criou a padrão entre o nosso SELECT e este INSERT —
        # a corrida que o índice parcial existe para barrar. O Postgres aborta a
        # transação inteira no IntegrityError, então o rollback é obrigatório
        # antes de qualquer query nova (senão: PendingRollbackError).
        await db.rollback()
        padrao = await _carteira_padrao(db, user.id)
        if padrao is None:
            raise  # o conflito existia no commit; sumir aqui é estado impossível
        return padrao

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
