from datetime import date
from decimal import Decimal

from app.models import Transacao
from app.services.posicoes_engine import calcular_posicao_em_data

FUTURO = date(2099, 1, 1)  # as_of bem à frente para não filtrar nada


def _tx(operacao, qtd, preco, d, custos="0", ticker="TEST"):
    """Cria uma Transacao (objeto ORM não persistido) para os testes."""
    return Transacao(
        ticker=ticker,
        operacao=operacao,
        quantidade=Decimal(str(qtd)),
        preco_unit=Decimal(str(preco)),
        outros_custos=Decimal(str(custos)),
        data=d,
    )


def test_somente_compras():
    txs = [
        _tx("compra", 100, 10, date(2024, 1, 1)),
        _tx("compra", 100, 20, date(2024, 2, 1)),
    ]
    p = calcular_posicao_em_data(txs, FUTURO)

    assert p.quantidade == Decimal(200)
    assert p.pm_historico == Decimal(15)  # (100*10 + 100*20) / 200
    assert p.ciclo_numero == 1
    assert p.ciclo_inicio == date(2024, 1, 1)


def test_compra_com_outros_custos():
    txs = [_tx("compra", 100, 10, date(2024, 1, 1), custos=50)]
    p = calcular_posicao_em_data(txs, FUTURO)

    assert p.quantidade == Decimal(100)
    assert p.pm_historico == Decimal("10.5")  # (1000 + 50) / 100


def test_compra_e_venda_parcial():
    txs = [
        _tx("compra", 100, 10, date(2024, 1, 1)),
        _tx("venda", 40, 12, date(2024, 3, 1)),
    ]
    p = calcular_posicao_em_data(txs, FUTURO)

    assert p.quantidade == Decimal(60)
    assert p.pm_historico == Decimal(10)  # venda não altera o PM
    assert p.ciclo_numero == 1
    assert p.ciclo_inicio == date(2024, 1, 1)


def test_venda_parcial_e_recompra_nao_infla_pm():
    # Regressão: numa venda parcial o custo acumulado deve cair proporcional-
    # mente. Sem isso, a recompra seguinte (sem zerar a posição) misturaria o
    # custo residual e inflaria o PM. Valores reais do WEGE3 (validados à mão).
    txs = [
        _tx("compra", 5, "46.814", date(2024, 1, 1)),
        _tx("compra", 10, "47.61", date(2024, 2, 1)),
        _tx("compra", 2, "44.72", date(2024, 3, 1)),
        _tx("venda", 5, "50", date(2024, 4, 1)),  # PM não muda aqui
        _tx("compra", 3, "43", date(2024, 5, 1)),
    ]
    p = calcular_posicao_em_data(txs, FUTURO)

    assert p.quantidade == Decimal(15)
    assert p.ciclo_numero == 1
    # Custo real ao PM médio, sem inflar: ≈ R$ 46,23 (não algo maior).
    assert abs(p.pm_historico - Decimal("46.23")) < Decimal("0.01")


def test_venda_total_e_recompra_reinicia_ciclo():
    txs = [
        _tx("compra", 100, 10, date(2024, 1, 1)),
        _tx("venda", 100, 15, date(2024, 6, 1)),
        _tx("compra", 50, 20, date(2024, 9, 1)),
    ]
    p = calcular_posicao_em_data(txs, FUTURO)

    assert p.quantidade == Decimal(50)
    # PM reiniciado no novo ciclo (20), não a média arrastada (~13,33).
    assert p.pm_historico == Decimal(20)
    assert p.ciclo_numero == 2
    assert p.ciclo_inicio == date(2024, 9, 1)


def test_venda_maior_que_posicao_trata_como_zero(caplog):
    txs = [
        _tx("compra", 100, 10, date(2024, 1, 1)),
        _tx("venda", 150, 12, date(2024, 6, 1)),
    ]
    with caplog.at_level("WARNING", logger="flow.posicoes"):
        p = calcular_posicao_em_data(txs, FUTURO)

    assert p.quantidade == Decimal(0)
    assert p.pm_historico == Decimal(0)
    assert p.ciclo_numero == 2
    assert p.ciclo_inicio is None
    assert "maior que a posição" in caplog.text


def test_as_of_ignora_transacoes_futuras():
    txs = [
        _tx("compra", 100, 10, date(2024, 1, 1)),
        _tx("compra", 100, 20, date(2024, 6, 1)),  # após o as_of
    ]
    p = calcular_posicao_em_data(txs, date(2024, 3, 1))

    assert p.quantidade == Decimal(100)
    assert p.pm_historico == Decimal(10)
    assert p.ciclo_inicio == date(2024, 1, 1)
