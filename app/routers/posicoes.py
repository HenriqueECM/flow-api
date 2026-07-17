from collections import defaultdict
from datetime import date
from decimal import ROUND_HALF_UP, Decimal

from fastapi import APIRouter, Depends
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.brapi_client import get_quotes
from app.core.db import get_db
from app.deps import get_owned_carteira
from app.models import Carteira, Transacao
from app.schemas import PosicaoOut
from app.services.posicoes_engine import PosicaoCalculada, calcular_posicao_em_data

router = APIRouter(prefix="/carteiras/{carteira_id}/posicoes", tags=["posicoes"])

_CENTS = Decimal("0.01")
_PM_QUANT = Decimal("0.0001")


@router.get("", response_model=list[PosicaoOut])
async def get_posicoes(
    carteira: Carteira = Depends(get_owned_carteira),
    db: AsyncSession = Depends(get_db),
) -> list[PosicaoOut]:
    """Consolida as transações por ticker (via motor de ciclos) e enriquece
    com a cotação atual.

    - Posição/PM calculados por `calcular_posicao_em_data` (as_of = hoje), que
      trata ciclos: venda total zera e a recompra reinicia o PM Histórico.
    - Posições com quantidade <= 0 são ignoradas (ciclo encerrado).
    - Preço atual/variação vêm da brapi.dev em uma única chamada em lote;
      quando ausentes, os campos derivados ficam nulos.
    """
    result = await db.execute(
        select(Transacao)
        .where(Transacao.carteira_id == carteira.id)
        .order_by(Transacao.data, Transacao.created_at)
    )
    transacoes = result.scalars().all()

    # Agrupa por ticker (preservando a ordem cronológica global da query).
    por_ticker: dict[str, list[Transacao]] = defaultdict(list)
    for tx in transacoes:
        por_ticker[tx.ticker.upper()].append(tx)

    hoje = date.today()
    calculadas: dict[str, PosicaoCalculada] = {}
    for ticker, txs in por_ticker.items():
        pos = calcular_posicao_em_data(txs, hoje)
        if pos.quantidade > 0:
            calculadas[ticker] = pos

    if not calculadas:
        return []

    # Cotações em lote (uma chamada só para todos os tickers da carteira).
    quotes = await get_quotes(list(calculadas.keys()))

    posicoes: list[PosicaoOut] = []
    for ticker in sorted(calculadas):
        pos = calculadas[ticker]
        quantidade = pos.quantidade
        pm = pos.pm_historico.quantize(_PM_QUANT, rounding=ROUND_HALF_UP)

        quote = quotes.get(ticker, {})
        preco_raw = quote.get("regularMarketPrice")
        variacao_raw = quote.get("regularMarketChangePercent")
        nome = quote.get("shortName") or ticker

        if preco_raw is not None:
            preco_atual = Decimal(str(preco_raw)).quantize(
                _CENTS, rounding=ROUND_HALF_UP
            )
            valor_total = (quantidade * preco_atual).quantize(
                _CENTS, rounding=ROUND_HALF_UP
            )
            lucro = (quantidade * (preco_atual - pm)).quantize(
                _CENTS, rounding=ROUND_HALF_UP
            )
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
