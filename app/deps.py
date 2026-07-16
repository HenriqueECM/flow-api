from uuid import UUID

from fastapi import Depends, HTTPException, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.db import get_db
from app.core.security import CurrentUser, get_current_user
from app.models import Carteira


async def get_owned_carteira(
    carteira_id: UUID,
    user: CurrentUser = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> Carteira:
    """Carrega a carteira garantindo que ela pertence ao usuário autenticado."""
    result = await db.execute(
        select(Carteira).where(Carteira.id == carteira_id, Carteira.user_id == user.id)
    )
    carteira = result.scalar_one_or_none()
    if carteira is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="Carteira não encontrada."
        )
    return carteira
