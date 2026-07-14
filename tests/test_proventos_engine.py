from datetime import date
from decimal import Decimal

from app.models import Transacao
from app.services.proventos_engine import calcular_campos_provento


def _tx(operacao, qtd, preco, d, custos="0", ticker="TEST"):
    return Transacao(
        ticker=ticker,
        operacao=operacao,
        quantidade=Decimal(str(qtd)),
        preco_unit=Decimal(str(preco)),
        outros_custos=Decimal(str(custos)),
        data=d,
    )


def test_provento_com_posicao_na_data_com():
    # 100 ações a PM 10 na Data COM.
    txs = [_tx("compra", 100, 10, date(2024, 1, 1))]
    calc = calcular_campos_provento(
        txs, data_com=date(2024, 6, 1), valor_por_acao=Decimal("0.5")
    )

    assert calc.quantidade == Decimal(100)
    assert calc.pm_historico == Decimal(10)
    assert calc.valor_recebido == Decimal(50)  # 100 * 0,50
    assert calc.yoc_evento == Decimal(5)  # (0,50 / 10) * 100


def test_provento_sem_transacao_antes_da_data_com():
    # A única compra é POSTERIOR à Data COM → sem posição na data.
    txs = [_tx("compra", 100, 10, date(2024, 7, 1))]
    calc = calcular_campos_provento(
        txs, data_com=date(2024, 6, 1), valor_por_acao=Decimal("0.5")
    )

    assert calc.quantidade is None
    assert calc.pm_historico is None
    assert calc.valor_recebido is None
    assert calc.yoc_evento is None


def test_provento_sem_nenhuma_transacao():
    calc = calcular_campos_provento(
        [], data_com=date(2024, 6, 1), valor_por_acao=Decimal("0.5")
    )
    assert calc.quantidade is None
    assert calc.yoc_evento is None


def test_data_com_em_ciclo_anterior_usa_pm_daquele_ciclo():
    # Ciclo 1: 100 @ 10. Venda total. Ciclo 2: 50 @ 20.
    txs = [
        _tx("compra", 100, 10, date(2024, 1, 1)),
        _tx("venda", 100, 15, date(2024, 3, 1)),
        _tx("compra", 50, 20, date(2024, 5, 1)),
    ]

    # Data COM dentro do ciclo 1 → usa PM 10 (não o PM 20 do ciclo atual).
    calc_ciclo1 = calcular_campos_provento(
        txs, data_com=date(2024, 2, 1), valor_por_acao=Decimal("1")
    )
    assert calc_ciclo1.quantidade == Decimal(100)
    assert calc_ciclo1.pm_historico == Decimal(10)
    assert calc_ciclo1.valor_recebido == Decimal(100)  # 100 * 1
    assert calc_ciclo1.yoc_evento == Decimal(10)  # (1 / 10) * 100

    # Data COM dentro do ciclo 2 → usa PM 20.
    calc_ciclo2 = calcular_campos_provento(
        txs, data_com=date(2024, 6, 1), valor_por_acao=Decimal("1")
    )
    assert calc_ciclo2.quantidade == Decimal(50)
    assert calc_ciclo2.pm_historico == Decimal(20)
    assert calc_ciclo2.yoc_evento == Decimal(5)  # (1 / 20) * 100


def test_pm_apos_venda_parcial_e_recompra_nao_infla_yoc():
    # Regressão via proventos: venda parcial reduz o custo proporcionalmente, então
    # a recompra (sem zerar) não infla o PM — e o YoC do evento fica correto.
    # compra 100@10 → venda 40 (custo 1000→600, PM 10) → compra 60@20 (PM 15).
    txs = [
        _tx("compra", 100, 10, date(2024, 1, 1)),
        _tx("venda", 40, 12, date(2024, 2, 1)),
        _tx("compra", 60, 20, date(2024, 3, 1)),
    ]
    calc = calcular_campos_provento(
        txs, data_com=date(2024, 6, 1), valor_por_acao=Decimal("1.5")
    )

    assert calc.quantidade == Decimal(120)
    assert calc.pm_historico == Decimal(15)  # não o ~18,33 do bug
    assert calc.valor_recebido == Decimal(180)  # 120 * 1,50
    assert calc.yoc_evento == Decimal(10)  # (1,50 / 15) * 100


def test_sem_data_com_retorna_tudo_nulo():
    txs = [_tx("compra", 100, 10, date(2024, 1, 1))]
    calc = calcular_campos_provento(txs, data_com=None, valor_por_acao=Decimal("0.5"))
    assert calc.quantidade is None
    assert calc.valor_recebido is None
