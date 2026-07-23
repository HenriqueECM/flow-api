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

    r = calcular_rentabilidade(transacoes, [], historico, {}, {}, date(2025, 1, 20))

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

    r = calcular_rentabilidade(transacoes, [], historico, {}, {}, date(2025, 2, 10))
    # Fev sem candle próprio → carry-forward do preço de Jan → 0% (sem variação).
    fev = next(m for m in r.meses if m.mes == "2025-02")
    assert fev.carteira == 0.0


def test_venda_total_e_mes_seguinte_vazio_sem_retorno():
    # Compra em Jan, vende tudo em Fev; Mar fica com a carteira zerada.
    transacoes = [
        _tx("compra", 100, 10, date(2025, 1, 10)),
        _tx("venda", 100, 12, date(2025, 2, 15)),
    ]
    historico = {"PETR4": {"2025-01": 10.0, "2025-02": 12.0, "2025-03": 12.0}}

    r = calcular_rentabilidade(transacoes, [], historico, {}, {}, date(2025, 3, 20))
    meses = {m.mes: m.carteira for m in r.meses}
    # Fev realizou o ganho da venda (base investida no mês → retorno definido).
    assert meses["2025-02"] == 50.0
    # Mar: carteira vazia (v_ini=0, sem fluxo) → sem base → None, não "0%".
    assert meses["2025-03"] is None


def test_provento_sem_data_ou_valor_e_ignorado():
    transacoes = [_tx("compra", 10, 10, date(2025, 1, 5))]
    historico = {"PETR4": {"2025-01": 10.0}}
    proventos = [
        _prov(5, None),  # sem data de referência
        Provento(
            ticker="PETR4",
            tipo_provento="Dividendo",
            data_com=date(2025, 1, 10),
            data_pagamento=date(2025, 1, 10),
            valor_por_acao=Decimal("0"),
            valor_recebido=None,  # sem valor recebido
        ),
    ]

    r = calcular_rentabilidade(
        transacoes, proventos, historico, {}, {}, date(2025, 1, 20)
    )
    # Nenhum provento entra → Jan segue 0% (comprou e fechou a 10).
    assert r.meses[0].carteira == 0.0


def test_sem_nenhum_preco_valora_pelo_pm():
    transacoes = [_tx("compra", 10, 10, date(2025, 1, 5))]

    # historico vazio → cada ativo é valorado pelo PM (custo). Comprou a 10 e
    # "vale" 10 → 0% no mês, em vez de anular a carteira.
    r = calcular_rentabilidade(transacoes, [], {}, {}, {}, date(2025, 1, 20))
    assert r.meses[0].carteira == 0.0
    assert r.cards.total == 0.0


def test_ticker_sem_historico_nao_zera_a_carteira():
    # PETR4 tem histórico (sobe 10→11); FIIX11 não tem — é valorado pelo PM.
    # A rentabilidade da carteira ainda reflete o movimento do que tem cotação,
    # em vez de virar None por causa do ticker sem histórico.
    transacoes = [
        _tx("compra", 100, 10, date(2025, 1, 2), ticker="PETR4"),
        _tx("compra", 100, 20, date(2025, 1, 2), ticker="FIIX11"),
    ]
    historico = {"PETR4": {"2025-01": 10.0, "2025-02": 11.0}}

    r = calcular_rentabilidade(transacoes, [], historico, {}, {}, date(2025, 2, 15))
    meses = {m.mes: m.carteira for m in r.meses}
    # Fev: PETR4 1000→1100 (+100); FIIX11 fica em 2000 (PM). Base 3000 → +3,33%.
    assert meses["2025-02"] == 3.3333


def test_preco_so_a_partir_de_mes_posterior_usa_pm_no_inicio():
    # Comprou em Jan, mas o histórico do ticker só começa em Fev: Jan não tem
    # preço anterior para herdar (carry-forward sem base) → valora pelo PM.
    transacoes = [_tx("compra", 10, 10, date(2025, 1, 5))]
    historico = {"PETR4": {"2025-02": 12.0}}

    r = calcular_rentabilidade(transacoes, [], historico, {}, {}, date(2025, 2, 20))
    meses = {m.mes: m.carteira for m in r.meses}
    # Jan valorado ao PM (10) → 0%. Fev com cotação (12) → +20%.
    assert meses["2025-01"] == 0.0
    assert meses["2025-02"] == 20.0
