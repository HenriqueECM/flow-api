"""Cálculo dos campos automáticos de um provento a partir da posição na Data COM.

Usa o motor de posição (com ciclos) para obter a quantidade e o PM Histórico
vigentes na Data COM, e deriva o valor recebido e o YoC do evento.
"""

from dataclasses import dataclass
from datetime import date
from decimal import ROUND_HALF_UP, Decimal

from app.models import Transacao
from app.services.posicoes_engine import calcular_posicao_em_data

_CENTS = Decimal("0.01")
_PM_QUANT = Decimal("0.0001")
_YOC_QUANT = Decimal("0.0001")


@dataclass
class ProventoCalculado:
    quantidade: Decimal | None
    pm_historico: Decimal | None
    valor_recebido: Decimal | None
    yoc_evento: Decimal | None


def calcular_campos_provento(
    transacoes: list[Transacao],
    data_com: date | None,
    valor_por_acao: Decimal,
) -> ProventoCalculado:
    """Calcula quantidade, PM, valor recebido e YoC do evento na Data COM.

    Se não há Data COM, ou não havia posição (quantidade <= 0) ou PM (<= 0) na
    data, todos os campos ficam nulos — o provento ainda pode ser salvo (o
    usuário pode registrar antes de sincronizar a posição).
    """
    if data_com is None:
        return ProventoCalculado(None, None, None, None)

    pos = calcular_posicao_em_data(transacoes, data_com)
    if pos.quantidade <= 0 or pos.pm_historico <= 0:
        return ProventoCalculado(None, None, None, None)

    quantidade = pos.quantidade
    pm_historico = pos.pm_historico.quantize(_PM_QUANT, rounding=ROUND_HALF_UP)
    valor_recebido = (pos.quantidade * valor_por_acao).quantize(
        _CENTS, rounding=ROUND_HALF_UP
    )
    yoc_evento = ((valor_por_acao / pos.pm_historico) * 100).quantize(
        _YOC_QUANT, rounding=ROUND_HALF_UP
    )

    return ProventoCalculado(
        quantidade=quantidade,
        pm_historico=pm_historico,
        valor_recebido=valor_recebido,
        yoc_evento=yoc_evento,
    )
