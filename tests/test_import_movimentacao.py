from decimal import Decimal

import pandas as pd

from app.schemas import ReviewRow
from app.services.import_movimentacao import (
    parse_dataframe,
    revalidar_lote,
    validar_posicao_lote,
)

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


def test_sequencia_com_bonificacao_e_fracao():
    # Compra 100 → bonificação +10 (pos 110) → fração -2 (pos 108) → venda 105.
    # Sem contar bonificação/fração no motor, a venda de 105 excederia a posição
    # (100) e viraria "erro" — o teste garante que ambos entram na sequência.
    df = _df([
        _linha("Credito", "05/01/2024", "Transferência - Liquidação", "PETR4 - PETROBRAS PN", 100, 30.0, 3000.0),
        _linha("Credito", "10/01/2024", "Bonificação em Ativos", "PETR4 - PETROBRAS PN", 10, "-", "-"),
        _linha("Debito", "15/01/2024", "Fração em Ativos", "PETR4 - PETROBRAS PN", 2, "-", "-"),
        _linha("Debito", "20/01/2024", "Transferência - Liquidação", "PETR4 - PETROBRAS PN", 105, 35.0, 3675.0),
    ])

    rows, summary = parse_dataframe(df)

    assert [r.status for r in rows] == ["valido", "valido", "valido", "valido"]
    assert summary.validas == 4
    assert summary.erros == 0
    assert summary.ignoradas == 0
    # Bonificação adiciona (mapeada como Compra); fração subtrai (Venda).
    assert rows[1].tipo == "Compra"
    assert rows[2].tipo == "Venda"
    # Bonificação/fração vêm sem custo (Preço/Valor "-" → 0).
    assert rows[1].preco_medio == 0
    assert rows[1].valor_total == 0


def test_fii_ignorado_sem_afetar_outros():
    # FII deve ser ignorado (só Ações por ora), sem interferir no cálculo de
    # posição de outros tickers no mesmo arquivo.
    df = _df([
        _linha("Credito", "05/01/2024", "Transferência - Liquidação", "MXRF11 - MAXI RENDA FDO INV IMOB - FII", 100, 10.0, 1000.0),
        _linha("Credito", "06/01/2024", "Transferência - Liquidação", "PETR4 - PETROBRAS PN", 50, 30.0, 1500.0),
        _linha("Debito", "07/01/2024", "Transferência - Liquidação", "PETR4 - PETROBRAS PN", 50, 32.0, 1600.0),
    ])

    rows, summary = parse_dataframe(df)

    assert rows[0].status == "ignorado"
    assert "FII" in rows[0].motivo
    # PETR4: compra 50 depois venda 50 — ambas válidas (FII não entra na posição).
    assert rows[1].status == "valido"
    assert rows[2].status == "valido"
    assert summary.ignoradas == 1
    assert summary.validas == 2
    assert summary.erros == 0


def test_fii_como_palavra_isolada_nao_substring():
    # "AFII" contém "fii" como substring, mas não como palavra isolada — deve
    # ser tratado normalmente (compra válida), não ignorado como FII.
    df = _df([
        _linha("Credito", "05/01/2024", "Transferência - Liquidação", "XPTO3 - EMPRESA AFII SA", 100, 10.0, 1000.0),
    ])

    rows, summary = parse_dataframe(df)

    assert rows[0].status == "valido"
    assert summary.ignoradas == 0


def test_tipos_ignorados_continuam_ignorados():
    df = _df([
        _linha("Credito", "05/01/2024", "Direito de Subscrição", "PETR4 - PETROBRAS PN", 5, "-", "-"),
        _linha("Debito", "06/01/2024", "Leilão de Fração", "VALE3 - VALE ON", 1, 60.0, 60.0),
    ])

    rows, summary = parse_dataframe(df)

    assert summary.ignoradas == 2
    assert all(r.status == "ignorado" for r in rows)
    assert "Direito de Subscrição" in rows[0].motivo


def test_ticker_nao_parseavel_vira_erro():
    df = _df([
        _linha("Debito", "05/01/2024", "Transferência - Liquidação", "", 100, 30.0, 3000.0),
    ])

    rows, summary = parse_dataframe(df)

    assert rows[0].status == "erro"
    assert "identificar o ticker" in rows[0].motivo
    assert summary.erros == 1


# ── Revalidação de linhas corrigidas manualmente (/revalidate) ───────────────


def test_revalidate_correcao_reabilita_linha_seguinte_do_mesmo_ticker():
    # Cenário: compra 100 → venda 150 (excede) → venda 40.
    # No preview, a 1ª venda excede (erro) e não consome posição, então a 2ª
    # venda de 40 ainda cabe (100 disponível) — fica válida.
    df = _df([
        _linha("Credito", "05/01/2024", "Transferência - Liquidação", "VALE3 - VALE ON", 100, 60.0, 6000.0),
        _linha("Debito", "06/01/2024", "Transferência - Liquidação", "VALE3 - VALE ON", 150, 65.0, 9750.0),
        _linha("Debito", "07/01/2024", "Transferência - Liquidação", "VALE3 - VALE ON", 40, 66.0, 2640.0),
    ])
    rows, summary = parse_dataframe(df)
    assert [r.status for r in rows] == ["valido", "erro", "valido"]

    # O usuário corrige a venda que excedia (150 → 80). Agora a sequência é
    # compra 100 → venda 80 (pos 20) → venda 40 (excede 20!). Sem reenviar o
    # arquivo, revalidamos a lista completa.
    editadas = list(rows)
    editadas[1] = rows[1].model_copy(update={"qtde": 80.0})

    revalidadas, resumo = revalidar_lote(editadas)

    # A venda corrigida passa a ser válida; a 3ª (40) agora excede o acumulado
    # (20) e vira erro — reclassificada em sequência, sem edição manual.
    assert [r.status for r in revalidadas] == ["valido", "valido", "erro"]
    assert "excede a posição" in revalidadas[2].motivo
    assert resumo.validas == 2
    assert resumo.erros == 1


def test_revalidate_correcao_menor_reabilita_linha_seguinte_valida():
    # Cenário do enunciado: venda excede posição; ao corrigir para uma qtde
    # menor, a própria linha vira válida E a linha seguinte do mesmo ticker,
    # que dava erro por causa do acumulado incorreto, vira válida sozinha.
    # Sequência: compra 100 → venda 90 → venda 20.
    df = _df([
        _linha("Credito", "05/01/2024", "Transferência - Liquidação", "ITUB4 - ITAU PN", 100, 30.0, 3000.0),
        _linha("Debito", "06/01/2024", "Transferência - Liquidação", "ITUB4 - ITAU PN", 90, 32.0, 2880.0),
        _linha("Debito", "07/01/2024", "Transferência - Liquidação", "ITUB4 - ITAU PN", 20, 33.0, 660.0),
    ])
    rows, summary = parse_dataframe(df)
    # Preview: venda 90 ok (pos 10); venda 20 excede 10 → erro.
    assert [r.status for r in rows] == ["valido", "valido", "erro"]

    # Usuário corrige a 1ª venda (90 → 50). Agora: pos 100 → 50 (pos 50) → 20 ok.
    editadas = list(rows)
    editadas[1] = rows[1].model_copy(update={"qtde": 50.0})

    revalidadas, resumo = revalidar_lote(editadas)

    # A venda seguinte (20) volta a caber automaticamente, sem ser editada.
    assert [r.status for r in revalidadas] == ["valido", "valido", "valido"]
    assert resumo.validas == 3
    assert resumo.erros == 0


def test_revalidate_mantem_ignorado_fora_da_posicao():
    # Linha ignorada permanece ignorada e não interfere na reclassificação.
    linhas = [
        ReviewRow(status="valido", ativo="PETR4", qtde=100.0, tipo="Compra", data="2024-01-05"),
        ReviewRow(status="ignorado", ativo="PETR4", qtde=50.0, tipo="", data="2024-01-06", motivo="Dividendo — não será importado"),
        ReviewRow(status="valido", ativo="PETR4", qtde=100.0, tipo="Venda", data="2024-01-07"),
    ]

    revalidadas, resumo = revalidar_lote(linhas)

    # A linha ignorada não somou/subtraiu posição: compra 100 → venda 100 ok.
    assert [r.status for r in revalidadas] == ["valido", "ignorado", "valido"]
    assert revalidadas[1].motivo == "Dividendo — não será importado"
    assert resumo.ignoradas == 1
    assert resumo.validas == 2
    assert resumo.erros == 0


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
