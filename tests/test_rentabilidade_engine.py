from datetime import date
from decimal import Decimal

from app.models import Provento, Transacao
from app.services.rentabilidade_engine import calcular_rentabilidade


def _tx(operacao, qtd, preco, d, custos="0", ticker="PETR4"):
    return Transacao(
        ticker=ticker,
        operacao=operacao,
        quantidade=Decimal(str(qtd)),
        preco_unit=Decimal(str(preco)),
        outros_custos=Decimal(str(custos)),
        data=d,
    )


def _prov(valor, d, ticker="PETR4"):
    return Provento(
        ticker=ticker,
        tipo_provento="Dividendo",
        data_com=d,
        data_pagamento=d,
        valor_por_acao=Decimal("0"),
        valor_recebido=Decimal(str(valor)),
    )


def test_sem_transacoes_retorna_vazio():
    r = calcular_rentabilidade([], [], {}, {}, {}, date(2025, 3, 15))
    assert r.meses == []
    assert r.tabela == []
    assert r.cards.total is None


def test_modified_dietz_mes_a_mes():
    # Jan: compra 100 @30 no dia 10; fecha o mês a 30 (sem ganho).
    # Fev: compra 50 @31 no dia 5; fecha a 31,5; recebe 50 de provento.
    # Mar: sem transação; fecha a 33.
    transacoes = [
        _tx("compra", 100, 30, date(2025, 1, 10)),
        _tx("compra", 50, 31, date(2025, 2, 5)),
    ]
    proventos = [_prov(50, date(2025, 2, 20))]
    historico = {"PETR4": {"2025-01": 30.0, "2025-02": 31.5, "2025-03": 33.0}}
    cdi = {"2025-01": 1.01, "2025-02": 0.99, "2025-03": 0.96}
    ibov = {"2025-01": 120000.0, "2025-02": 122000.0, "2025-03": 125000.0}

    r = calcular_rentabilidade(
        transacoes, proventos, historico, cdi, ibov, date(2025, 3, 15)
    )

    assert [m.mes for m in r.meses] == ["2025-01", "2025-02", "2025-03"]
    # Jan: comprou e fechou no mesmo preço → 0%.
    assert r.meses[0].carteira == 0.0
    # Fev: ganho ≈ 225 sobre base ≈ 4328,6 (Modified Dietz) → ~5,2%.
    assert r.meses[1].carteira == 5.198
    # Mar: 150×(33−31,5)=225 sobre 4725 → 4,7619%.
    assert r.meses[2].carteira == 4.7619
    # CDI passa direto; IBOV derivado de 2 fechamentos (Jan não tem anterior).
    assert r.meses[0].cdi == 1.01
    assert r.meses[0].ibov is None
    assert r.meses[1].ibov == 1.6667

    # Card total = composto dos 3 meses.
    assert r.cards.total == 10.2074
    assert r.cards.mes == 4.7619
    # vs CDI em p.p. (10,2074 − composto do CDI).
    assert r.cards.total_vs_cdi == 7.2181


def test_tabela_por_ano_e_acumulado():
    transacoes = [_tx("compra", 10, 100, date(2024, 11, 4))]
    historico = {"PETR4": {"2024-11": 100.0, "2024-12": 110.0, "2025-01": 121.0}}

    r = calcular_rentabilidade(
        transacoes, [], historico, {}, {}, date(2025, 1, 20)
    )

    anos = {linha.ano: linha for linha in r.tabela}
    assert set(anos) == {2024, 2025}
    # 2024: Nov = 0% (comprou e fechou a 100), Dez = +10%.
    assert anos[2024].meses[10] == 0.0  # índice 10 = Novembro
    assert anos[2024].meses[11] == 10.0
    assert anos[2024].acum == 10.0
    # 2025: Jan = +10% (110 → 121).
    assert anos[2025].meses[0] == 10.0


def test_mes_sem_preco_fica_sem_retorno():
    transacoes = [_tx("compra", 10, 50, date(2025, 1, 6))]
    # Sem preço de Fev → carry-forward usa Jan (50), retorno 0; sem nenhum preço
    # o mês ficaria None. Aqui há Jan, então Fev valora por carry-forward.
    historico = {"PETR4": {"2025-01": 50.0}}

    r = calcular_rentabilidade(
        transacoes, [], historico, {}, {}, date(2025, 2, 10)
    )
    # Fev sem candle próprio → carry-forward do preço de Jan → 0% (sem variação).
    fev = next(m for m in r.meses if m.mes == "2025-02")
    assert fev.carteira == 0.0
