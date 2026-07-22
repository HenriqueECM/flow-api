"""Rentabilidade da carteira mês a mês, comparada a benchmarks (CDI, IBOV).

A pergunta que a tela responde é "quanto a carteira rendeu em cada mês", separando
o ganho de mercado dos aportes/resgates. O método usado é o **Modified Dietz**, o
padrão de mercado para retorno ponderado no tempo quando não se tem valorização
diária: cada fluxo externo do mês (compra = entrada, venda = saída) é ponderado
pela fração do mês em que esteve investido.

    R_mes = (V_fim − V_ini − F + P) / (V_ini + Σ wᵢ·Fᵢ)

onde:
    V_ini  valor de mercado da carteira no fim do mês anterior
    V_fim  valor de mercado da carteira no fim do mês
    F      fluxo externo líquido do mês (Σ Fᵢ; compra > 0, venda < 0)
    wᵢ     peso temporal do fluxo i = (dias_no_mês − dia_i + 1) / dias_no_mês
    P      proventos recebidos no mês (ganho que não é fluxo externo)

O retorno acumulado encadeia os retornos mensais: Π(1 + Rᵢ) − 1.

Valoração usa o fechamento REAL de cada mês (preço da época × quantidade da época).
Meses sem preço para algum ativo em carteira usam o último preço conhecido
(carry-forward); se nem isso existir, o mês fica sem retorno (None) em vez de
inventar valor.
"""

from calendar import monthrange
from collections import defaultdict
from dataclasses import dataclass, field
from datetime import date
from decimal import Decimal

from app.services.posicoes_engine import calcular_posicao_em_data


# ── Estruturas de saída ───────────────────────────────────────────────────────


@dataclass
class MesRetorno:
    """Retorno percentual de um mês para cada série (None = sem dado)."""

    mes: str  # "YYYY-MM"
    carteira: float | None
    cdi: float | None
    ibov: float | None


@dataclass
class AnoLinha:
    """Linha da tabela mensal: 12 meses (Jan→Dez) + acumulado do ano."""

    ano: int
    meses: list[float | None]  # sempre 12 posições
    acum: float | None


@dataclass
class CardsRentabilidade:
    total: float | None  # acumulado da carteira desde o início
    doze_meses: float | None  # acumulado dos últimos 12 meses
    mes: float | None  # último mês fechado/parcial
    total_vs_cdi: float | None  # p.p. acima/abaixo do CDI no mesmo período
    doze_meses_vs_cdi: float | None
    mes_vs_cdi: float | None


@dataclass
class RelatorioRentabilidade:
    meses: list[MesRetorno] = field(default_factory=list)
    tabela: list[AnoLinha] = field(default_factory=list)
    cards: CardsRentabilidade = field(
        default_factory=lambda: CardsRentabilidade(None, None, None, None, None, None)
    )


# ── Utilidades de calendário ──────────────────────────────────────────────────


def _chave_mes(ano: int, mes: int) -> str:
    return f"{ano}-{mes:02d}"


def _meses_entre(inicio: tuple[int, int], fim: tuple[int, int]) -> list[tuple[int, int]]:
    """Lista de (ano, mês) de `inicio` a `fim`, inclusive."""
    ano, mes = inicio
    ano_fim, mes_fim = fim
    out: list[tuple[int, int]] = []
    while (ano, mes) <= (ano_fim, mes_fim):
        out.append((ano, mes))
        mes += 1
        if mes > 12:
            mes = 1
            ano += 1
    return out


def _ultimo_dia(ano: int, mes: int, hoje: date) -> date:
    """Fim do mês (para posicionar transações); no mês corrente, usa `hoje`."""
    if ano == hoje.year and mes == hoje.month:
        return hoje
    return date(ano, mes, monthrange(ano, mes)[1])


# ── Preço com carry-forward ───────────────────────────────────────────────────


class _PrecoLookup:
    """Preço de fechamento de um ticker em um mês, com carry-forward do último
    preço conhecido para meses sem candle (feriado longo, baixa liquidez, etc.)."""

    def __init__(self, serie: dict[str, float]) -> None:
        self._meses = sorted(serie.keys())
        self._serie = serie

    def preco(self, chave_mes: str) -> Decimal | None:
        exato = self._serie.get(chave_mes)
        if exato is not None:
            return Decimal(str(exato))
        # Carry-forward: último mês conhecido <= chave pedida.
        anterior: str | None = None
        for m in self._meses:
            if m <= chave_mes:
                anterior = m
            else:
                break
        if anterior is None:
            return None
        return Decimal(str(self._serie[anterior]))


# ── Cálculo principal ─────────────────────────────────────────────────────────


@dataclass
class _FluxoMes:
    liquido: Decimal = Decimal(0)  # Σ Fᵢ
    ponderado: Decimal = Decimal(0)  # Σ wᵢ·Fᵢ


def _acumular(retornos: list[float | None]) -> float | None:
    """Encadeia retornos mensais (%) ignorando None: Π(1+r) − 1, em %."""
    fator = Decimal(1)
    houve = False
    for r in retornos:
        if r is None:
            continue
        fator *= Decimal(1) + Decimal(str(r)) / Decimal(100)
        houve = True
    if not houve:
        return None
    return round(float((fator - Decimal(1)) * Decimal(100)), 4)


def _pp(a: float | None, b: float | None) -> float | None:
    """Diferença em pontos percentuais (a − b), ou None se faltar algum lado."""
    if a is None or b is None:
        return None
    return round(a - b, 4)


def calcular_rentabilidade(
    transacoes: list,
    proventos: list,
    historico: dict[str, dict[str, float]],
    cdi: dict[str, float],
    ibov: dict[str, float],
    hoje: date,
) -> RelatorioRentabilidade:
    """Monta o relatório de rentabilidade da carteira e dos benchmarks.

    - `transacoes`: todas as transações da carteira, ordenadas por (data, created_at).
    - `proventos`: todos os proventos da carteira.
    - `historico`: {ticker: {"YYYY-MM": close}} (brapi).
    - `cdi`: {"YYYY-MM": retorno_mensal_%} (BACEN).
    - `ibov`: {"YYYY-MM": close} (Yahoo).
    - `hoje`: data de referência (mês corrente e janela de 12m).
    """
    if not transacoes:
        return RelatorioRentabilidade()

    # Janela: do mês da 1ª transação até o mês corrente.
    primeira = min(tx.data for tx in transacoes)
    meses = _meses_entre((primeira.year, primeira.month), (hoje.year, hoje.month))

    # Transações agrupadas por ticker (para posição em cada data) e por mês (fluxos).
    por_ticker: dict[str, list] = defaultdict(list)
    for tx in transacoes:
        por_ticker[tx.ticker.upper()].append(tx)

    fluxo_por_mes: dict[str, _FluxoMes] = defaultdict(_FluxoMes)
    for tx in transacoes:
        chave = _chave_mes(tx.data.year, tx.data.month)
        bruto = Decimal(tx.quantidade) * Decimal(tx.preco_unit)
        custos = Decimal(tx.outros_custos)
        # Compra: dinheiro que entra (+). Venda: dinheiro que sai (−).
        valor = (bruto + custos) if tx.operacao == "compra" else -(bruto - custos)
        dias = monthrange(tx.data.year, tx.data.month)[1]
        peso = Decimal(dias - tx.data.day + 1) / Decimal(dias)
        f = fluxo_por_mes[chave]
        f.liquido += valor
        f.ponderado += peso * valor

    # Proventos recebidos por mês (ganho interno, não é fluxo externo).
    proventos_por_mes: dict[str, Decimal] = defaultdict(Decimal)
    for p in proventos:
        ref = p.data_pagamento or p.data_com
        if ref is None or p.valor_recebido is None:
            continue
        proventos_por_mes[_chave_mes(ref.year, ref.month)] += Decimal(p.valor_recebido)

    precos = {tk: _PrecoLookup(serie) for tk, serie in historico.items()}

    def valor_carteira(chave_mes: str, as_of: date) -> Decimal | None:
        """Valor de mercado da carteira no fim do mês. None se faltar preço de
        algum ativo com posição aberta na data."""
        total = Decimal(0)
        for tk, txs in por_ticker.items():
            pos = calcular_posicao_em_data(txs, as_of)
            if pos.quantidade <= 0:
                continue
            lookup = precos.get(tk)
            preco = lookup.preco(chave_mes) if lookup else None
            if preco is None:
                return None  # sem preço → não dá para valorar o mês
            total += pos.quantidade * preco
        return total

    # Retorno mês a mês da carteira (Modified Dietz).
    ret_carteira: list[float | None] = []
    v_ini = Decimal(0)  # valor no fim do mês anterior (0 antes de começar)
    for ano, mes in meses:
        chave = _chave_mes(ano, mes)
        as_of = _ultimo_dia(ano, mes, hoje)
        v_fim = valor_carteira(chave, as_of)

        f = fluxo_por_mes.get(chave, _FluxoMes())
        prov = proventos_por_mes.get(chave, Decimal(0))

        if v_fim is None:
            ret_carteira.append(None)
            # Mantém v_ini anterior (melhor que zerar) para o próximo mês.
            continue

        denom = v_ini + f.ponderado
        if denom > 0:
            ganho = v_fim - v_ini - f.liquido + prov
            ret_carteira.append(round(float(ganho / denom * Decimal(100)), 4))
        else:
            # Sem base investida no mês (carteira vazia) → sem retorno.
            ret_carteira.append(None)
        v_ini = v_fim

    # Retorno mês a mês do CDI (já vem em %) e do IBOV (derivado de 2 closes).
    ret_cdi: list[float | None] = [
        (round(v, 4) if (v := cdi.get(_chave_mes(a, m))) is not None else None)
        for a, m in meses
    ]

    ret_ibov: list[float | None] = []
    for ano, mes in meses:
        atual = ibov.get(_chave_mes(ano, mes))
        # Mês anterior no calendário.
        pa, pm = (ano, mes - 1) if mes > 1 else (ano - 1, 12)
        anterior = ibov.get(_chave_mes(pa, pm))
        if atual is None or anterior is None or anterior == 0:
            ret_ibov.append(None)
        else:
            ret_ibov.append(round((atual / anterior - 1) * 100, 4))

    serie_meses = [
        MesRetorno(mes=_chave_mes(a, m), carteira=rc, cdi=rd, ibov=ri)
        for (a, m), rc, rd, ri in zip(meses, ret_carteira, ret_cdi, ret_ibov)
    ]

    # Tabela mensal por ano (só a carteira).
    anos = sorted({a for a, _ in meses})
    ret_por_chave = {
        _chave_mes(a, m): rc for (a, m), rc in zip(meses, ret_carteira)
    }
    tabela: list[AnoLinha] = []
    for ano in anos:
        linha = [ret_por_chave.get(_chave_mes(ano, m)) for m in range(1, 13)]
        tabela.append(AnoLinha(ano=ano, meses=linha, acum=_acumular(linha)))

    # Cards. Janela de 12m = últimos 12 meses da série.
    total = _acumular(ret_carteira)
    doze = _acumular(ret_carteira[-12:])
    ult = ret_carteira[-1] if ret_carteira else None
    cdi_total = _acumular(ret_cdi)
    cdi_doze = _acumular(ret_cdi[-12:])
    cdi_ult = ret_cdi[-1] if ret_cdi else None

    cards = CardsRentabilidade(
        total=total,
        doze_meses=doze,
        mes=ult,
        total_vs_cdi=_pp(total, cdi_total),
        doze_meses_vs_cdi=_pp(doze, cdi_doze),
        mes_vs_cdi=_pp(ult, cdi_ult),
    )

    return RelatorioRentabilidade(meses=serie_meses, tabela=tabela, cards=cards)
