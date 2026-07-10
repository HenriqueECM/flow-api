from decimal import Decimal

from fastapi import APIRouter, Depends
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.db import get_db
from app.deps import get_owned_carteira
from app.models import Carteira, Transacao
from app.schemas import Posicao

router = APIRouter(prefix="/carteiras/{carteira_id}/posicoes", tags=["posicoes"])


@router.get("", response_model=list[Posicao])
async def get_posicoes(
    carteira: Carteira = Depends(get_owned_carteira),
    db: AsyncSession = Depends(get_db),
) -> list[Posicao]:
    """Consolida as transações por ticker: quantidade líquida e preço médio.

    Preço médio = custo total das compras (incl. outros custos) / qtd comprada.
    Quantidade = comprada - vendida.
    """
    result = await db.execute(
        select(Transacao).where(Transacao.carteira_id == carteira.id)
    )
    transacoes = result.scalars().all()

    qtd_liquida: dict[str, Decimal] = {}
    qtd_comprada: dict[str, Decimal] = {}
    custo_compras: dict[str, Decimal] = {}

    for tx in transacoes:
        sinal = Decimal(1) if tx.operacao == "compra" else Decimal(-1)
        qtd_liquida[tx.ticker] = qtd_liquida.get(tx.ticker, Decimal(0)) + sinal * tx.quantidade
        if tx.operacao == "compra":
            qtd_comprada[tx.ticker] = qtd_comprada.get(tx.ticker, Decimal(0)) + tx.quantidade
            custo_compras[tx.ticker] = (
                custo_compras.get(tx.ticker, Decimal(0))
                + tx.quantidade * tx.preco_unit
                + tx.outros_custos
            )

    posicoes: list[Posicao] = []
    for ticker, qtd in qtd_liquida.items():
        if qtd <= 0:
            continue  # posição zerada/vendida
        comprada = qtd_comprada.get(ticker, Decimal(0))
        custo = custo_compras.get(ticker, Decimal(0))
        preco_medio = (custo / comprada) if comprada > 0 else Decimal(0)
        posicoes.append(
            Posicao(
                ticker=ticker,
                quantidade=qtd,
                preco_medio=preco_medio.quantize(Decimal("0.0001")),
                custo_total=(qtd * preco_medio).quantize(Decimal("0.01")),
            )
        )

    posicoes.sort(key=lambda p: p.ticker)
    return posicoes
