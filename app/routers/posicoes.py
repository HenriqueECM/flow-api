from decimal import ROUND_HALF_UP, Decimal

from fastapi import APIRouter, Depends
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.brapi_client import get_quotes
from app.core.db import get_db
from app.deps import get_owned_carteira
from app.models import Carteira, Transacao
from app.schemas import PosicaoOut

router = APIRouter(prefix="/carteiras/{carteira_id}/posicoes", tags=["posicoes"])

_CENTS = Decimal("0.01")
_PM_QUANT = Decimal("0.0001")


@router.get("", response_model=list[PosicaoOut])
async def get_posicoes(
    carteira: Carteira = Depends(get_owned_carteira),
    db: AsyncSession = Depends(get_db),
) -> list[PosicaoOut]:
    """Consolida as transações por ticker e enriquece com a cotação atual.

    - Quantidade = comprada - vendida.
    - PM Histórico = custo total das compras (incl. outros custos) / qtd comprada.
      Vendas não alteram o PM (regra de negócio do projeto).
    - Posições com quantidade líquida <= 0 são ignoradas (ciclo encerrado).
    - Preço atual/variação vêm da brapi.dev em uma única chamada em lote;
      quando ausentes, os campos derivados ficam nulos.
    """
    result = await db.execute(
        select(Transacao)
        .where(Transacao.carteira_id == carteira.id)
        .order_by(Transacao.data, Transacao.created_at)
    )
    transacoes = result.scalars().all()

    qtd_liquida: dict[str, Decimal] = {}
    qtd_comprada: dict[str, Decimal] = {}
    custo_compras: dict[str, Decimal] = {}

    for tx in transacoes:
        ticker = tx.ticker.upper()
        sinal = Decimal(1) if tx.operacao == "compra" else Decimal(-1)
        qtd_liquida[ticker] = qtd_liquida.get(ticker, Decimal(0)) + sinal * tx.quantidade
        if tx.operacao == "compra":
            qtd_comprada[ticker] = qtd_comprada.get(ticker, Decimal(0)) + tx.quantidade
            custo_compras[ticker] = (
                custo_compras.get(ticker, Decimal(0))
                + tx.quantidade * tx.preco_unit
                + tx.outros_custos
            )

    # Só posições abertas (quantidade líquida positiva).
    abertas = {tk: q for tk, q in qtd_liquida.items() if q > 0}
    if not abertas:
        return []

    # Cotações em lote (uma chamada só para todos os tickers da carteira).
    quotes = await get_quotes(list(abertas.keys()))

    posicoes: list[PosicaoOut] = []
    for ticker in sorted(abertas):
        quantidade = abertas[ticker]
        comprada = qtd_comprada.get(ticker, Decimal(0))
        custo = custo_compras.get(ticker, Decimal(0))
        pm = (custo / comprada) if comprada > 0 else Decimal(0)
        pm = pm.quantize(_PM_QUANT, rounding=ROUND_HALF_UP)

        quote = quotes.get(ticker, {})
        preco_raw = quote.get("regularMarketPrice")
        variacao_raw = quote.get("regularMarketChangePercent")
        nome = quote.get("shortName") or ticker

        if preco_raw is not None:
            preco_atual = Decimal(str(preco_raw)).quantize(_CENTS, rounding=ROUND_HALF_UP)
            valor_total = (quantidade * preco_atual).quantize(_CENTS, rounding=ROUND_HALF_UP)
            lucro = (quantidade * (preco_atual - pm)).quantize(_CENTS, rounding=ROUND_HALF_UP)
        else:
            preco_atual = valor_total = lucro = None

        variacao_percent = float(variacao_raw) if variacao_raw is not None else None

        posicoes.append(
            PosicaoOut(
                ticker=ticker,
                nome=nome,
                quantidade=quantidade,
                pm_historico=pm,
                preco_atual=preco_atual,
                variacao_percent=variacao_percent,
                valor_total=valor_total,
                lucro=lucro,
            )
        )

    return posicoes
