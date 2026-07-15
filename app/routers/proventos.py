from datetime import date
from decimal import Decimal

from fastapi import APIRouter, Depends, status
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.db import get_db
from app.deps import get_owned_carteira
from app.models import Carteira, Provento, Transacao
from app.schemas import ProventoCreate, ProventoOut, ProventoPreviewOut
from app.services.proventos_engine import (
    calcular_campos_provento,
    liquidar_valor_recebido,
    liquidar_yoc,
)

router = APIRouter(prefix="/carteiras/{carteira_id}/proventos", tags=["proventos"])


def _provento_out(p: Provento) -> ProventoOut:
    """Serializa um provento aplicando a retenção de IR do JCP ao valor recebido
    e ao YoC. Os campos persistidos guardam o valor bruto; o líquido é derivado
    na leitura (sem recomputar a posição)."""
    return ProventoOut(
        id=p.id,
        carteira_id=p.carteira_id,
        ticker=p.ticker,
        tipo_provento=p.tipo_provento,
        data_com=p.data_com,
        data_pagamento=p.data_pagamento,
        valor_por_acao=p.valor_por_acao,
        quantidade=p.quantidade,
        pm_historico=p.pm_historico,
        valor_recebido=liquidar_valor_recebido(p.tipo_provento, p.valor_recebido),
        yoc_evento=liquidar_yoc(p.tipo_provento, p.yoc_evento),
        created_at=p.created_at,
    )


async def _transacoes_do_ticker(
    db: AsyncSession, carteira_id, ticker: str
) -> list[Transacao]:
    """Transações do ticker na carteira, em ordem cronológica."""
    result = await db.execute(
        select(Transacao)
        .where(
            Transacao.carteira_id == carteira_id,
            func.upper(Transacao.ticker) == ticker.upper(),
        )
        .order_by(Transacao.data, Transacao.created_at)
    )
    return list(result.scalars().all())


@router.get("", response_model=list[ProventoOut])
async def list_proventos(
    carteira: Carteira = Depends(get_owned_carteira),
    db: AsyncSession = Depends(get_db),
) -> list[ProventoOut]:
    result = await db.execute(
        select(Provento)
        .where(Provento.carteira_id == carteira.id)
        .order_by(Provento.data_pagamento.desc().nullslast(), Provento.created_at.desc())
    )
    return [_provento_out(p) for p in result.scalars().all()]


@router.get("/preview", response_model=ProventoPreviewOut)
async def preview_provento(
    carteira: Carteira = Depends(get_owned_carteira),
    db: AsyncSession = Depends(get_db),
    ticker: str | None = None,
    data_com: date | None = None,
    valor_por_acao: Decimal | None = None,
    tipo_provento: str | None = None,
) -> ProventoPreviewOut:
    """Calcula os campos do provento em tempo real, sem persistir.

    Chamado enquanto o usuário digita — parâmetros incompletos (sem ticker ou
    sem Data COM) retornam tudo nulo, não erro. Sem `valor_por_acao`, ainda
    devolve quantidade/PM na data; valor recebido e YoC ficam nulos.

    Informar `tipo_provento` faz o valor recebido/YoC já virem líquidos de IR
    quando for JCP (coerente com a listagem); sem ele, vêm brutos.
    """
    if not ticker or data_com is None:
        return ProventoPreviewOut()

    transacoes = await _transacoes_do_ticker(db, carteira.id, ticker)
    calc = calcular_campos_provento(
        transacoes, data_com, valor_por_acao if valor_por_acao is not None else Decimal(0)
    )

    tem_valor = valor_por_acao is not None
    return ProventoPreviewOut(
        quantidade=calc.quantidade,
        pm_historico=calc.pm_historico,
        valor_recebido=(
            liquidar_valor_recebido(tipo_provento, calc.valor_recebido)
            if tem_valor
            else None
        ),
        yoc_evento=(
            liquidar_yoc(tipo_provento, calc.yoc_evento) if tem_valor else None
        ),
    )


@router.post("", response_model=ProventoOut, status_code=status.HTTP_201_CREATED)
async def create_provento(
    payload: ProventoCreate,
    carteira: Carteira = Depends(get_owned_carteira),
    db: AsyncSession = Depends(get_db),
) -> ProventoOut:
    # Posição (quantidade e PM) vigente na Data COM, a partir das transações.
    transacoes = await _transacoes_do_ticker(db, carteira.id, payload.ticker)
    calc = calcular_campos_provento(
        transacoes, payload.data_com, payload.valor_por_acao
    )

    # Persiste o valor bruto (fato imutável); o líquido de IR é derivado na
    # leitura (_provento_out), igual à listagem.
    provento = Provento(
        carteira_id=carteira.id,
        ticker=payload.ticker,
        tipo_provento=payload.tipo_provento,
        data_com=payload.data_com,
        data_pagamento=payload.data_pagamento,
        valor_por_acao=payload.valor_por_acao,
        quantidade=calc.quantidade,
        pm_historico=calc.pm_historico,
        valor_recebido=calc.valor_recebido,
        yoc_evento=calc.yoc_evento,
    )
    db.add(provento)
    await db.commit()
    await db.refresh(provento)
    return _provento_out(provento)
