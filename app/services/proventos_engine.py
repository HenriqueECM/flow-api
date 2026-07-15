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

# JCP (Juros sobre Capital Próprio) sofre 17,5% de IRRF retido na fonte, então o
# valor líquido recebido é 82,5% do bruto (quantidade × valor por ação). Dividendos
# e rendimentos são isentos para PF (líquido = bruto).
ALIQUOTA_IR_JCP = Decimal("0.175")


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


def eh_jcp(tipo_provento: str | None) -> bool:
    """True se o tipo indica Juros sobre Capital Próprio (match tolerante:
    'jcp' ou 'juros sobre capital', case-insensitive)."""
    texto = (tipo_provento or "").lower()
    return "jcp" in texto or "juros sobre capital" in texto


def liquidar_valor_recebido(
    tipo_provento: str | None, valor_bruto: Decimal | None
) -> Decimal | None:
    """Valor líquido de IR: para JCP, retém 17,5% (líquido = bruto × 0,825). Demais
    tipos (dividendo/rendimento) são isentos e passam inalterados. None → None."""
    if valor_bruto is None:
        return None
    if eh_jcp(tipo_provento):
        return (valor_bruto * (1 - ALIQUOTA_IR_JCP)).quantize(
            _CENTS, rounding=ROUND_HALF_UP
        )
    return valor_bruto


def liquidar_yoc(
    tipo_provento: str | None, yoc_bruto: Decimal | None
) -> Decimal | None:
    """YoC líquido de IR, coerente com o valor recebido líquido: para JCP reduz
    na mesma proporção do imposto retido (× 0,825). None → None."""
    if yoc_bruto is None:
        return None
    if eh_jcp(tipo_provento):
        return (yoc_bruto * (1 - ALIQUOTA_IR_JCP)).quantize(
            _YOC_QUANT, rounding=ROUND_HALF_UP
        )
    return yoc_bruto
