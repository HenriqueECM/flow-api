from decimal import Decimal

import pandas as pd

from app.services.import_movimentacao import parse_dataframe, validar_posicao_lote

COLS = [
    "Entrada/Saída",
    "Data",
    "Movimentação",
    "Produto",
    "Instituição",
    "Quantidade",
    "Preço unitário",
    "Valor da Operação",
]


def _linha(entrada_saida, data, mov, produto, qtd, preco, valor):
    return {
        "Entrada/Saída": entrada_saida,
        "Data": data,
        "Movimentação": mov,
        "Produto": produto,
        "Instituição": "XP",
        "Quantidade": qtd,
        "Preço unitário": preco,
        "Valor da Operação": valor,
    }


def _df(linhas):
    return pd.DataFrame(linhas, columns=COLS)


def test_validas_e_ignoradas():
    df = _df([
        _linha("Credito", "05/01/2024", "Transferência - Liquidação", "PETR4 - PETROBRAS PN", 100, 30.0, 3000.0),
        _linha("Credito", "10/01/2024", "Dividendo", "PETR4 - PETROBRAS PN", 100, 0.5, 50.0),
    ])

    rows, summary = parse_dataframe(df)

    assert summary.total == 2
    assert summary.validas == 1
    assert summary.ignoradas == 1
    assert summary.erros == 0

    compra = rows[0]
    assert compra.status == "valido"
    assert compra.ativo == "PETR4"
    assert compra.tipo == "Compra"
    assert compra.data == "2024-01-05"
    assert compra.motivo is None

    ignorada = rows[1]
    assert ignorada.status == "ignorado"
    assert "Dividendo" in ignorada.motivo


def test_venda_maior_que_posicao_vira_erro():
    df = _df([
        _linha("Credito", "05/01/2024", "Transferência - Liquidação", "VALE3 - VALE ON", 100, 60.0, 6000.0),
        _linha("Debito", "06/01/2024", "Transferência - Liquidação", "VALE3 - VALE ON", 150, 65.0, 9750.0),
    ])

    rows, summary = parse_dataframe(df)

    assert rows[0].status == "valido"
    assert rows[1].status == "erro"
    assert "excede a posição" in rows[1].motivo
    assert summary.erros == 1
    assert summary.validas == 1


def test_reversao_ordem_cronologica():
    # Arquivo em ordem decrescente (B3): venda (mais recente) antes da compra.
    # Sem reversão, a venda seria processada primeiro e viraria erro.
    df = _df([
        _linha("Debito", "10/02/2024", "Transferência - Liquidação", "ITUB4 - ITAU PN", 50, 32.0, 1600.0),
        _linha("Credito", "05/01/2024", "Transferência - Liquidação", "ITUB4 - ITAU PN", 100, 30.0, 3000.0),
    ])

    rows, summary = parse_dataframe(df)

    # Após reversão, a ordem processada é compra → venda, ambas válidas.
    assert [r.tipo for r in rows] == ["Compra", "Venda"]
    assert [r.data for r in rows] == ["2024-01-05", "2024-02-10"]
    assert summary.validas == 2
    assert summary.erros == 0


def test_credito_e_compra_debito_e_venda():
    # Credito = ação entrando na custódia = compra.
    # Debito = ação saindo da custódia = venda.
    df = _df([
        _linha("Credito", "05/01/2024", "Transferência - Liquidação", "BBAS3 - BRASIL ON", 100, 20.0, 2000.0),
        _linha("Debito", "10/01/2024", "Transferência - Liquidação", "BBAS3 - BRASIL ON", 40, 22.0, 880.0),
    ])

    rows, summary = parse_dataframe(df)

    assert rows[0].tipo == "Compra"
    assert rows[0].status == "valido"
    assert rows[1].tipo == "Venda"
    assert rows[1].status == "valido"
    assert summary.validas == 2
    assert summary.erros == 0


def test_ticker_nao_parseavel_vira_erro():
    df = _df([
        _linha("Debito", "05/01/2024", "Transferência - Liquidação", "", 100, 30.0, 3000.0),
    ])

    rows, summary = parse_dataframe(df)

    assert rows[0].status == "erro"
    assert "identificar o ticker" in rows[0].motivo
    assert summary.erros == 1


# ── Validação de posição no lote (usada no /confirm) ─────────────────────────


def test_lote_venda_isolada_falha_sem_bloquear_demais():
    # Posição inicial (banco): PETR4 = 100.
    posicao = {"PETR4": Decimal(100)}
    itens = [
        ("PETR4", "venda", Decimal(60)),   # ok  → disp 100→40
        ("PETR4", "venda", Decimal(60)),   # excede 40 → falha (não altera)
        ("PETR4", "compra", Decimal(100)), # ok  → 40→140
        ("PETR4", "venda", Decimal(100)),  # ok  → 140→40
    ]

    motivos = validar_posicao_lote(posicao, itens)

    assert motivos[0] is None
    assert motivos[1] is not None and "excede a posição" in motivos[1]
    assert motivos[2] is None
    assert motivos[3] is None
    # A venda que falhou não consumiu posição: sobra 40 no fim.
    assert posicao["PETR4"] == Decimal(40)


def test_lote_sem_posicao_inicial_venda_excede():
    posicao: dict[str, Decimal] = {}
    itens = [
        ("VALE3", "compra", Decimal(100)),  # ok → 100
        ("VALE3", "venda", Decimal(150)),   # excede 100 → falha
    ]

    motivos = validar_posicao_lote(posicao, itens)

    assert motivos[0] is None
    assert motivos[1] is not None
    assert posicao["VALE3"] == Decimal(100)
