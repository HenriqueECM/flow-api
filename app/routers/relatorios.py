from collections import defaultdict
from datetime import date
from decimal import ROUND_HALF_UP, Decimal

from fastapi import APIRouter, Depends
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.db import get_db
from app.deps import get_owned_carteira
from app.models import Carteira, Provento, Transacao
from app.schemas import RelatorioYocOut
from app.services.posicoes_engine import calcular_posicao_em_data
from app.services.relatorios_engine import AtivoPosicao, calcular_relatorio_yoc

router = APIRouter(prefix="/carteiras/{carteira_id}/relatorios", tags=["relatorios"])

_PM_QUANT = Decimal("0.0001")


@router.get("/yoc", response_model=RelatorioYocOut)
async def get_relatorio_yoc(
    carteira: Carteira = Depends(get_owned_carteira),
    db: AsyncSession = Depends(get_db),
    ticker: str | None = None,
    data_inicio: date | None = None,
    data_fim: date | None = None,
) -> RelatorioYocOut:
    """YoC consolidado por ativo e da carteira, ponderado por evento.

    - Lista os ativos com posição aberta (quantidade > 0), via motor de ciclos,
      só para exibir quantidade e PM atuais.
    - O YoC de cada ativo e o consolidado usam os campos persistidos de cada
      provento (quantidade, PM histórico, valor recebido no momento da criação),
      então um provento de ciclo antigo contribui com o seu próprio PM.
    - `ticker`, `data_inicio` e `data_fim` são filtros opcionais do KPI: afetam
      apenas `consolidado.valor_recebido_total`/`yoc_total`. A tabela `ativos` e
      os campos de 12m são sempre retornados sem filtro.
    """
    # Transações → posições abertas por ticker.
    result = await db.execute(
        select(Transacao)
        .where(Transacao.carteira_id == carteira.id)
        .order_by(Transacao.data, Transacao.created_at)
    )
    transacoes = result.scalars().all()

    por_ticker: dict[str, list[Transacao]] = defaultdict(list)
    for tx in transacoes:
        por_ticker[tx.ticker.upper()].append(tx)

    hoje = date.today()
    ativos: list[AtivoPosicao] = []
    for tk, txs in por_ticker.items():
        pos = calcular_posicao_em_data(txs, hoje)
        if pos.quantidade <= 0:
            continue
        # Nome mais recente informado nas transações; cai para o ticker se nulo.
        nome = next((t.nome for t in reversed(txs) if t.nome), tk)
        ativos.append(
            AtivoPosicao(
                ticker=tk,
                nome=nome,
                quantidade=pos.quantidade,
                pm_historico=pos.pm_historico.quantize(_PM_QUANT, rounding=ROUND_HALF_UP),
            )
        )
    ativos.sort(key=lambda a: a.ticker)  # saída estável

    # Proventos de todos os tickers da carteira, agrupados.
    result_prov = await db.execute(
        select(Provento).where(Provento.carteira_id == carteira.id)
    )
    proventos_por_ticker: dict[str, list[Provento]] = defaultdict(list)
    for p in result_prov.scalars().all():
        proventos_por_ticker[p.ticker.upper()].append(p)

    relatorio = calcular_relatorio_yoc(
        ativos,
        proventos_por_ticker,
        hoje,
        ticker=ticker,
        data_inicio=data_inicio,
        data_fim=data_fim,
    )
    return RelatorioYocOut.model_validate(relatorio)
