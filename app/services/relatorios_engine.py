"""Relatório de YoC (Yield on Cost) consolidado, por ativo e da carteira.

O YoC é ponderado **por evento** (não pela posição atual): cada provento
contribui com o seu próprio custo-base histórico — quantidade × PM Histórico
que ficaram persistidos no momento da criação do provento (imutáveis). Assim um
provento de um ciclo já encerrado mantém o PM daquele ciclo, e não é
"recalculado" ao PM atual do ativo.

    yoc = soma(valor_recebido dos eventos) / soma(quantidade × pm_historico dos
          mesmos eventos) × 100

O valor recebido é considerado **líquido de IR** (JCP retém 17,5% na fonte), igual
à listagem de proventos. Como a retenção é aplicada no numerador por evento
antes de agregar, os YoCs resultantes já saem líquidos automaticamente.
"""

from dataclasses import dataclass
from datetime import date
from decimal import ROUND_HALF_UP, Decimal

from app.models import Provento
from app.services.proventos_engine import liquidar_valor_recebido

_CENTS = Decimal("0.01")
_YOC_QUANT = Decimal("0.0001")


@dataclass
class AtivoPosicao:
    """Entrada: posição aberta de um ativo (só para exibição — não entra no YoC)."""

    ticker: str
    nome: str
    quantidade: Decimal
    pm_historico: Decimal


@dataclass
class YocAtivo:
    ticker: str
    nome: str
    quantidade_atual: Decimal
    pm_historico_atual: Decimal
    valor_recebido_12m: Decimal
    valor_recebido_total: Decimal
    valor_recebido_ano: Decimal
    yoc_12m: Decimal | None
    yoc_total: Decimal | None
    yoc_ano: Decimal | None


@dataclass
class YocConsolidado:
    valor_recebido_12m: Decimal
    valor_recebido_total: Decimal
    yoc_12m: Decimal | None
    yoc_total: Decimal | None


@dataclass
class RelatorioYoc:
    ativos: list[YocAtivo]
    consolidado: YocConsolidado


def _data_referencia(p: Provento) -> date | None:
    """Data que posiciona o evento na janela de 12m: pagamento, ou COM se nulo."""
    return p.data_pagamento or p.data_com


def _um_ano_atras(d: date) -> date:
    try:
        return d.replace(year=d.year - 1)
    except ValueError:  # 29/02 → 28/02 no ano anterior
        return d.replace(year=d.year - 1, day=28)


def _yoc(recebido: Decimal, base: Decimal) -> Decimal | None:
    """YoC (%) ou None quando não há custo-base (nenhum provento no período)."""
    if base <= 0:
        return None
    return (recebido / base * 100).quantize(_YOC_QUANT, rounding=ROUND_HALF_UP)


class _Acumulador:
    """Somatórios de valor recebido e custo-base (quantidade × PM) por período."""

    def __init__(self) -> None:
        self.recebido_12m = Decimal(0)
        self.recebido_total = Decimal(0)
        self.recebido_ano = Decimal(0)
        self.base_12m = Decimal(0)
        self.base_total = Decimal(0)
        self.base_ano = Decimal(0)

    def adicionar(self, p: Provento, dentro_12m: bool, dentro_ano: bool) -> None:
        # Valor líquido de IR (JCP retém 17,5%); demais tipos passam inalterados.
        # Netar aqui, no numerador, faz o YoC agregado já sair líquido.
        recebido = liquidar_valor_recebido(p.tipo_provento, p.valor_recebido) or Decimal(0)
        # Custo-base ao PM histórico persistido do evento (imutável). Se o
        # provento foi criado sem posição sincronizada (campos nulos), não há
        # custo-base: contribui 0 e não distorce o YoC.
        if p.quantidade is not None and p.pm_historico is not None:
            base = p.quantidade * p.pm_historico
        else:
            base = Decimal(0)

        self.recebido_total += recebido
        self.base_total += base
        if dentro_12m:
            self.recebido_12m += recebido
            self.base_12m += base
        if dentro_ano:
            self.recebido_ano += recebido
            self.base_ano += base


def calcular_relatorio_yoc(
    ativos: list[AtivoPosicao],
    proventos_por_ticker: dict[str, list[Provento]],
    hoje: date,
) -> RelatorioYoc:
    """Monta o relatório de YoC.

    - `ativos`: posições abertas (quantidade > 0) — definem quais tickers entram
      no relatório e fornecem quantidade/PM atuais (apenas exibição).
    - `proventos_por_ticker`: proventos já persistidos, agrupados por ticker
      (chave em maiúsculas).
    - `hoje`: referência para a janela de 12 meses.

    O consolidado agrega TODOS os eventos dos ativos listados (soma total
    recebida / soma total do custo-base), não é a média simples dos YoCs.
    """
    corte_12m = _um_ano_atras(hoje)
    geral = _Acumulador()
    linhas: list[YocAtivo] = []

    for ativo in ativos:
        acc = _Acumulador()
        for p in proventos_por_ticker.get(ativo.ticker.upper(), []):
            ref = _data_referencia(p)
            dentro_12m = ref is not None and ref >= corte_12m
            dentro_ano = ref is not None and ref.year == hoje.year
            acc.adicionar(p, dentro_12m, dentro_ano)
            geral.adicionar(p, dentro_12m, dentro_ano)

        linhas.append(
            YocAtivo(
                ticker=ativo.ticker,
                nome=ativo.nome,
                quantidade_atual=ativo.quantidade,
                pm_historico_atual=ativo.pm_historico,
                valor_recebido_12m=acc.recebido_12m.quantize(_CENTS, rounding=ROUND_HALF_UP),
                valor_recebido_total=acc.recebido_total.quantize(_CENTS, rounding=ROUND_HALF_UP),
                valor_recebido_ano=acc.recebido_ano.quantize(_CENTS, rounding=ROUND_HALF_UP),
                yoc_12m=_yoc(acc.recebido_12m, acc.base_12m),
                yoc_total=_yoc(acc.recebido_total, acc.base_total),
                yoc_ano=_yoc(acc.recebido_ano, acc.base_ano),
            )
        )

    consolidado = YocConsolidado(
        valor_recebido_12m=geral.recebido_12m.quantize(_CENTS, rounding=ROUND_HALF_UP),
        valor_recebido_total=geral.recebido_total.quantize(_CENTS, rounding=ROUND_HALF_UP),
        yoc_12m=_yoc(geral.recebido_12m, geral.base_12m),
        yoc_total=_yoc(geral.recebido_total, geral.base_total),
    )
    return RelatorioYoc(ativos=linhas, consolidado=consolidado)
