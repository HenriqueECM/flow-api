"""Motor central de cálculo de posição em uma data específica.

Suporta ciclos: quando a posição de um ativo zera (venda total), o ciclo é
encerrado e uma eventual recompra reinicia o PM Histórico do zero — evitando
que o preço médio "arraste" custos de um ciclo já encerrado.
"""

import logging
from dataclasses import dataclass
from datetime import date
from decimal import Decimal

from app.models import Transacao

logger = logging.getLogger("flow.posicoes")


@dataclass
class PosicaoCalculada:
    quantidade: Decimal
    pm_historico: Decimal
    # Ciclo vigente (1 = primeiro). Incrementa a cada vez que a posição zera.
    ciclo_numero: int
    # Data da primeira compra do ciclo vigente (None se não há posição aberta).
    ciclo_inicio: date | None


def calcular_posicao_em_data(
    transacoes: list[Transacao],
    as_of: date,
) -> PosicaoCalculada:
    """Calcula a posição consolidada de um ativo até a data `as_of`.

    Espera `transacoes` já ordenadas por (data, created_at). Considera apenas
    as com data <= as_of. Compras atualizam quantidade/custo/PM; vendas apenas
    reduzem a quantidade (não afetam o PM). Ao zerar, o ciclo é encerrado.
    """
    quantidade = Decimal(0)
    custo = Decimal(0)  # custo acumulado das compras do ciclo vigente
    pm_historico = Decimal(0)  # recalculado só nas compras; venda não altera
    ciclo_numero = 1
    ciclo_inicio: date | None = None

    for tx in transacoes:
        if tx.data > as_of:
            continue

        if tx.operacao == "compra":
            # Primeira compra do ciclo vigente marca o início do ciclo.
            if quantidade == 0:
                ciclo_inicio = tx.data
            quantidade += tx.quantidade
            custo += tx.quantidade * tx.preco_unit + tx.outros_custos
            pm_historico = custo / quantidade
        else:  # venda — não afeta custo nem PM
            quantidade -= tx.quantidade

            if quantidade <= 0:
                if quantidade < 0:
                    logger.warning(
                        "Venda maior que a posição (ticker=%s, data=%s): "
                        "quantidade ficou %s; tratando como 0 (dado inconsistente).",
                        getattr(tx, "ticker", "?"),
                        tx.data,
                        quantidade,
                    )
                # Ciclo encerrado: zera estado e prepara o próximo ciclo.
                quantidade = Decimal(0)
                custo = Decimal(0)
                pm_historico = Decimal(0)
                ciclo_numero += 1
                ciclo_inicio = None

    return PosicaoCalculada(
        quantidade=quantidade,
        pm_historico=pm_historico,
        ciclo_numero=ciclo_numero,
        ciclo_inicio=ciclo_inicio,
    )
