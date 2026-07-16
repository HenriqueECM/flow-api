"""Relatório de YoC (Yield on Cost) consolidado, por ativo e da carteira.

O YoC é a **soma simples** do `yoc_evento` já persistido em cada provento (o YoC
que foi calculado e gravado na criação do evento) — sem ponderar por quantidade
ou custo-base. Dois eventos de 10% e 2% somam 12%.

    yoc = soma(yoc_evento dos eventos do recorte)

O `yoc_evento` persistido é BRUTO; a retenção de IR do JCP (17,5%) é aplicada por
evento antes de somar, mantendo o relatório líquido e coerente com a listagem de
proventos. `valor_recebido` continua sendo soma simples (também líquida de IR).
"""

from dataclasses import dataclass
from datetime import date
from decimal import ROUND_HALF_UP, Decimal

from app.models import Provento
from app.services.proventos_engine import liquidar_valor_recebido, liquidar_yoc

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
    """Data que posiciona o evento no período: pagamento, ou COM se nulo."""
    return p.data_pagamento or p.data_com


def _um_ano_atras(d: date) -> date:
    try:
        return d.replace(year=d.year - 1)
    except ValueError:  # 29/02 → 28/02 no ano anterior
        return d.replace(year=d.year - 1, day=28)


def _passa_filtro(
    p: Provento,
    ref: date | None,
    ticker: str | None,
    data_inicio: date | None,
    data_fim: date | None,
) -> bool:
    """Se o evento entra no recorte (ticker opcional + intervalo de datas).

    A data de referência é a mesma regra do 12m: pagamento, ou COM se nulo.
    Eventos sem data de referência só entram quando não há filtro de data.
    """
    if ticker is not None and p.ticker.upper() != ticker.upper():
        return False
    if ref is None:
        return data_inicio is None and data_fim is None
    if data_inicio is not None and ref < data_inicio:
        return False
    if data_fim is not None and ref > data_fim:
        return False
    return True


def _soma_yoc(soma: Decimal, tem_evento: bool) -> Decimal | None:
    """Soma simples de yoc_evento (%), ou None se não houve evento com YoC."""
    if not tem_evento:
        return None
    return soma.quantize(_YOC_QUANT, rounding=ROUND_HALF_UP)


class _Acumulador:
    """Somas simples de valor recebido e de yoc_evento por período (12m/total/ano),
    ambos líquidos de IR. `tem_yoc_*` distingue "sem evento" (→ None) de "soma 0"."""

    def __init__(self) -> None:
        self.recebido_12m = Decimal(0)
        self.recebido_total = Decimal(0)
        self.recebido_ano = Decimal(0)
        self.yoc_12m = Decimal(0)
        self.yoc_total = Decimal(0)
        self.yoc_ano = Decimal(0)
        self.tem_yoc_12m = False
        self.tem_yoc_total = False
        self.tem_yoc_ano = False

    def adicionar(
        self, p: Provento, *, total: bool = False, m12: bool = False, ano: bool = False
    ) -> None:
        # Valores líquidos de IR (JCP retém 17,5%); demais tipos passam inalterados.
        recebido = liquidar_valor_recebido(p.tipo_provento, p.valor_recebido) or Decimal(0)
        yoc = liquidar_yoc(p.tipo_provento, p.yoc_evento)  # None se yoc_evento nulo

        if total:
            self.recebido_total += recebido
            if yoc is not None:
                self.yoc_total += yoc
                self.tem_yoc_total = True
        if m12:
            self.recebido_12m += recebido
            if yoc is not None:
                self.yoc_12m += yoc
                self.tem_yoc_12m = True
        if ano:
            self.recebido_ano += recebido
            if yoc is not None:
                self.yoc_ano += yoc
                self.tem_yoc_ano = True


def calcular_relatorio_yoc(
    ativos: list[AtivoPosicao],
    proventos_por_ticker: dict[str, list[Provento]],
    hoje: date,
    *,
    ticker: str | None = None,
    data_inicio: date | None = None,
    data_fim: date | None = None,
) -> RelatorioYoc:
    """Monta o relatório de YoC como SOMA SIMPLES dos `yoc_evento` persistidos.

    - `ativos`: posições abertas (quantidade > 0) — definem quais tickers aparecem
      na tabela e fornecem quantidade/PM atuais (apenas exibição, não entram no YoC).
    - `proventos_por_ticker`: proventos já persistidos, agrupados por ticker (chave
      em maiúsculas).
    - `hoje`: referência para a janela de 12 meses e para o ano-calendário.

    Filtros opcionais:
    - `data_inicio`/`data_fim`: recorte aplicado às colunas `total` (tanto por
      ativo quanto no consolidado). As colunas de 12m e ano têm janela fixa e não
      são afetadas.
    - `ticker`: afeta APENAS `consolidado.*_total`. Com ticker, o consolidado
      considera TODOS os proventos da carteira (inclui ativos já vendidos); sem
      ticker, só os dos ativos com posição aberta (a carteira atual).
    """
    corte_12m = _um_ano_atras(hoje)
    geral = _Acumulador()  # consolidado 12m (posições abertas, janela fixa)
    linhas: list[YocAtivo] = []

    for ativo in ativos:
        acc = _Acumulador()
        for p in proventos_por_ticker.get(ativo.ticker.upper(), []):
            ref = _data_referencia(p)
            m12 = ref is not None and ref >= corte_12m
            ano = ref is not None and ref.year == hoje.year
            # A coluna `total` do ativo respeita o recorte de datas (sem ticker).
            no_total = _passa_filtro(p, ref, None, data_inicio, data_fim)
            acc.adicionar(p, total=no_total, m12=m12, ano=ano)
            geral.adicionar(p, m12=m12)  # consolidado 12m

        linhas.append(
            YocAtivo(
                ticker=ativo.ticker,
                nome=ativo.nome,
                quantidade_atual=ativo.quantidade,
                pm_historico_atual=ativo.pm_historico,
                valor_recebido_12m=acc.recebido_12m.quantize(_CENTS, rounding=ROUND_HALF_UP),
                valor_recebido_total=acc.recebido_total.quantize(_CENTS, rounding=ROUND_HALF_UP),
                valor_recebido_ano=acc.recebido_ano.quantize(_CENTS, rounding=ROUND_HALF_UP),
                yoc_12m=_soma_yoc(acc.yoc_12m, acc.tem_yoc_12m),
                yoc_total=_soma_yoc(acc.yoc_total, acc.tem_yoc_total),
                yoc_ano=_soma_yoc(acc.yoc_ano, acc.tem_yoc_ano),
            )
        )

    # Consolidado `total` (KPI): população depende do filtro de ticker. Com ticker
    # específico, varre TODOS os proventos da carteira; sem ticker, só os dos
    # ativos com posição aberta. O recorte de datas se aplica nos dois casos.
    if ticker is not None:
        fonte_filtro = [p for lista in proventos_por_ticker.values() for p in lista]
    else:
        fonte_filtro = [
            p
            for ativo in ativos
            for p in proventos_por_ticker.get(ativo.ticker.upper(), [])
        ]

    filtrado = _Acumulador()
    for p in fonte_filtro:
        ref = _data_referencia(p)
        if _passa_filtro(p, ref, ticker, data_inicio, data_fim):
            filtrado.adicionar(p, total=True)

    # 12m sem filtro (janela fixa); total restrito ao recorte do KPI.
    consolidado = YocConsolidado(
        valor_recebido_12m=geral.recebido_12m.quantize(_CENTS, rounding=ROUND_HALF_UP),
        valor_recebido_total=filtrado.recebido_total.quantize(_CENTS, rounding=ROUND_HALF_UP),
        yoc_12m=_soma_yoc(geral.yoc_12m, geral.tem_yoc_12m),
        yoc_total=_soma_yoc(filtrado.yoc_total, filtrado.tem_yoc_total),
    )
    return RelatorioYoc(ativos=linhas, consolidado=consolidado)
