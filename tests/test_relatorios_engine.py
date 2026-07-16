from datetime import date
from decimal import Decimal

from app.models import Provento
from app.services.relatorios_engine import AtivoPosicao, calcular_relatorio_yoc

HOJE = date(2024, 7, 1)  # corte de 12m: 2023-07-01


def _ativo(ticker, quantidade, pm, nome=None):
    return AtivoPosicao(
        ticker=ticker,
        nome=nome or ticker,
        quantidade=Decimal(str(quantidade)),
        pm_historico=Decimal(str(pm)),
    )


def _prov(ticker, recebido, yoc, data_pag=None, data_com=None, tipo="Dividendo"):
    """Provento já persistido. O YoC do relatório é a SOMA SIMPLES do `yoc_evento`
    persistido; quantidade/PM não entram mais no cálculo. `recebido` e `yoc` são
    os valores BRUTOS gravados — a retenção de IR (JCP) é aplicada no relatório."""
    return Provento(
        ticker=ticker,
        tipo_provento=tipo,
        data_com=data_com,
        data_pagamento=data_pag,
        valor_por_acao=Decimal("0"),
        quantidade=None,
        pm_historico=None,
        valor_recebido=Decimal(str(recebido)) if recebido is not None else None,
        yoc_evento=Decimal(str(yoc)) if yoc is not None else None,
    )


def test_yoc_e_soma_simples_dos_yoc_evento():
    ativos = [_ativo("WEGE3", 100, 20)]
    proventos = {
        "WEGE3": [
            _prov("WEGE3", recebido=50, yoc="5.0000", data_pag=date(2024, 3, 1)),
            _prov("WEGE3", recebido=30, yoc="3.0000", data_pag=date(2024, 5, 1)),
        ],
    }

    rel = calcular_relatorio_yoc(ativos, proventos, HOJE)
    ativo = rel.ativos[0]

    # Soma simples: 5 + 3 = 8% (sem média, sem ponderação por custo).
    assert ativo.yoc_total == Decimal("8.0000")
    assert ativo.yoc_12m == Decimal("8.0000")
    assert ativo.valor_recebido_total == Decimal("80.00")
    assert ativo.valor_recebido_12m == Decimal("80.00")
    # Campos de exibição preservados (não entram no cálculo).
    assert ativo.quantidade_atual == Decimal("100")
    assert ativo.pm_historico_atual == Decimal("20")


def test_ativo_sem_proventos_tem_yoc_nulo_e_recebido_zero():
    ativos = [_ativo("PETR4", 100, 30)]

    rel = calcular_relatorio_yoc(ativos, {}, HOJE)
    ativo = rel.ativos[0]

    assert ativo.quantidade_atual == Decimal(100)
    assert ativo.valor_recebido_12m == Decimal("0.00")
    assert ativo.valor_recebido_total == Decimal("0.00")
    # None (não 0) indica "sem dado ainda".
    assert ativo.yoc_12m is None
    assert ativo.yoc_total is None
    # Consolidado sem nenhum evento também fica nulo.
    assert rel.consolidado.yoc_total is None


def test_separa_12m_de_total():
    # Recente (dentro de 12m) e antigo (>12m). Total soma os dois; 12m só o recente.
    ativos = [_ativo("BBAS3", 100, 10)]
    proventos = {
        "BBAS3": [
            _prov("BBAS3", recebido=60, yoc="6.0000", data_pag=date(2024, 3, 1)),  # 12m
            _prov("BBAS3", recebido=50, yoc="5.0000", data_pag=date(2022, 1, 1)),  # fora
        ],
    }

    rel = calcular_relatorio_yoc(ativos, proventos, HOJE)
    ativo = rel.ativos[0]

    # 12m: só o recente → 6%.
    assert ativo.yoc_12m == Decimal("6.0000")
    assert ativo.valor_recebido_12m == Decimal("60.00")
    # Total: soma dos dois → 6 + 5 = 11%.
    assert ativo.yoc_total == Decimal("11.0000")
    assert ativo.valor_recebido_total == Decimal("110.00")


def test_usa_data_com_quando_pagamento_e_nulo():
    # Sem data_pagamento, a janela de 12m usa a data_com.
    ativos = [_ativo("ITUB4", 100, 10)]
    proventos = {
        "ITUB4": [_prov("ITUB4", recebido=40, yoc="4.0000", data_com=date(2024, 2, 1))],
    }

    rel = calcular_relatorio_yoc(ativos, proventos, HOJE)
    ativo = rel.ativos[0]

    assert ativo.yoc_12m == Decimal("4.0000")
    assert ativo.valor_recebido_12m == Decimal("40.00")


def test_yoc_evento_nulo_nao_estabelece_presenca():
    # Provento sem YoC persistido (posição não sincronizada na criação) não conta
    # como "evento com YoC": se for o único, o YoC do período fica null.
    ativos = [_ativo("SANB11", 100, 10)]
    proventos = {
        "SANB11": [_prov("SANB11", recebido=25, yoc=None, data_pag=date(2024, 3, 1))],
    }

    rel = calcular_relatorio_yoc(ativos, proventos, HOJE)
    ativo = rel.ativos[0]

    assert ativo.yoc_12m is None
    assert ativo.yoc_total is None
    # Mas o valor recebido ainda soma normalmente.
    assert ativo.valor_recebido_total == Decimal("25.00")


def test_consolidado_e_soma_simples_nao_ponderada():
    # Exemplo do enunciado: 10% + 2% = 12% (não média 6%, não ponderação por custo).
    ativos = [_ativo("AAAA3", 100, 10), _ativo("BBBB3", 100, 50)]
    proventos = {
        "AAAA3": [_prov("AAAA3", recebido=100, yoc="10.0000", data_pag=date(2024, 1, 15))],
        "BBBB3": [_prov("BBBB3", recebido=100, yoc="2.0000", data_pag=date(2024, 1, 15))],
    }

    rel = calcular_relatorio_yoc(ativos, proventos, HOJE)
    por_ticker = {a.ticker: a for a in rel.ativos}

    assert por_ticker["AAAA3"].yoc_total == Decimal("10.0000")
    assert por_ticker["BBBB3"].yoc_total == Decimal("2.0000")

    cons = rel.consolidado
    assert cons.valor_recebido_total == Decimal("200.00")
    assert cons.yoc_total == Decimal("12.0000")  # 10 + 2, soma direta
    assert cons.yoc_12m == Decimal("12.0000")
    assert cons.yoc_total != Decimal("6.0000")  # não é média
    assert cons.yoc_total != Decimal("3.3333")  # não é ponderação por custo


# ── Retenção de IR do JCP (aplicada por evento antes de somar) ────────────────


def test_jcp_entra_liquido_na_soma():
    # yoc_evento persistido é BRUTO (10%); JCP retém 17,5% → soma entra 8,25%.
    ativos = [_ativo("TAEE11", 100, 10)]
    proventos = {
        "TAEE11": [
            _prov("TAEE11", recebido=100, yoc="10.0000", data_pag=date(2024, 3, 1), tipo="JCP"),
        ],
    }

    rel = calcular_relatorio_yoc(ativos, proventos, HOJE)
    ativo = rel.ativos[0]

    assert ativo.yoc_total == Decimal("8.2500")  # 10 × 0,825
    assert ativo.yoc_12m == Decimal("8.2500")
    assert ativo.yoc_ano == Decimal("8.2500")
    assert ativo.valor_recebido_total == Decimal("82.50")
    assert rel.consolidado.yoc_total == Decimal("8.2500")


def test_consolidado_soma_jcp_liquido_com_dividendo():
    # JCP 10% → 8,25% líquido; Dividendo 4% isento. Soma simples = 12,25%.
    ativos = [_ativo("AAAA3", 100, 10), _ativo("BBBB3", 100, 10)]
    proventos = {
        "AAAA3": [_prov("AAAA3", recebido=100, yoc="10.0000", data_pag=date(2024, 1, 15), tipo="JCP")],
        "BBBB3": [_prov("BBBB3", recebido=100, yoc="4.0000", data_pag=date(2024, 1, 15), tipo="Dividendo")],
    }

    rel = calcular_relatorio_yoc(ativos, proventos, HOJE)
    por_ticker = {a.ticker: a for a in rel.ativos}

    assert por_ticker["AAAA3"].yoc_total == Decimal("8.2500")
    assert por_ticker["BBBB3"].yoc_total == Decimal("4.0000")

    cons = rel.consolidado
    assert cons.valor_recebido_total == Decimal("182.50")
    assert cons.yoc_total == Decimal("12.2500")  # 8,25 + 4


# ── Campos do ano-calendário atual ───────────────────────────────────────────


def test_valor_recebido_ano_e_yoc_ano_do_ano_calendario():
    # HOJE = 2024-07-01 → ano-calendário 2024.
    ativos = [_ativo("BBAS3", 100, 10)]
    proventos = {
        "BBAS3": [
            _prov("BBAS3", recebido=40, yoc="4.0000", data_pag=date(2024, 3, 1)),  # 2024: ano + 12m
            _prov("BBAS3", recebido=30, yoc="3.0000", data_pag=date(2023, 8, 1)),  # 12m, mas ano anterior
        ],
    }

    rel = calcular_relatorio_yoc(ativos, proventos, HOJE)
    ativo = rel.ativos[0]

    # Ano 2024: só o evento de março → 4%.
    assert ativo.yoc_ano == Decimal("4.0000")
    assert ativo.valor_recebido_ano == Decimal("40.00")
    # 12m soma os dois: 4 + 3 = 7%.
    assert ativo.yoc_12m == Decimal("7.0000")
    assert ativo.valor_recebido_12m == Decimal("70.00")


def test_yoc_ano_null_quando_nao_recebeu_no_ano():
    # Único provento é de ano anterior → nada no ano-calendário atual.
    ativos = [_ativo("EGIE3", 100, 10)]
    proventos = {
        "EGIE3": [_prov("EGIE3", recebido=50, yoc="5.0000", data_pag=date(2023, 5, 1))],
    }

    rel = calcular_relatorio_yoc(ativos, proventos, HOJE)
    ativo = rel.ativos[0]

    assert ativo.valor_recebido_ano == Decimal("0.00")
    assert ativo.yoc_ano is None  # None (não 0) = sem recebimento no ano
    # Ainda contribui no acumulado total.
    assert ativo.valor_recebido_total == Decimal("50.00")
    assert ativo.yoc_total == Decimal("5.0000")


# ── Filtros do KPI: ticker (consolidado) + intervalo de datas (total) ─────────


def _cenario_multi():
    # AAAA3: 4% (2024-03) + 6% (2023-05).  BBBB3: 5% (2024-02).
    ativos = [_ativo("AAAA3", 100, 10), _ativo("BBBB3", 100, 10)]
    proventos = {
        "AAAA3": [
            _prov("AAAA3", recebido=40, yoc="4.0000", data_pag=date(2024, 3, 1)),
            _prov("AAAA3", recebido=60, yoc="6.0000", data_pag=date(2023, 5, 1)),
        ],
        "BBBB3": [
            _prov("BBBB3", recebido=50, yoc="5.0000", data_pag=date(2024, 2, 1)),
        ],
    }
    return ativos, proventos


def test_sem_filtro_consolidado_soma_todos_os_eventos():
    ativos, proventos = _cenario_multi()
    rel = calcular_relatorio_yoc(ativos, proventos, HOJE)
    cons = rel.consolidado
    # 4 + 6 + 5 = 15% (soma direta).
    assert cons.valor_recebido_total == Decimal("150.00")
    assert cons.yoc_total == Decimal("15.0000")


def test_filtro_so_por_ticker_afeta_apenas_consolidado_total():
    ativos, proventos = _cenario_multi()
    rel = calcular_relatorio_yoc(ativos, proventos, HOJE, ticker="AAAA3")
    cons = rel.consolidado

    # Só AAAA3: 4 + 6 = 10%.
    assert cons.valor_recebido_total == Decimal("100.00")
    assert cons.yoc_total == Decimal("10.0000")
    # 12m permanece SEM filtro (eventos de 2024: 4 + 5 = 9%).
    assert cons.valor_recebido_12m == Decimal("90.00")
    assert cons.yoc_12m == Decimal("9.0000")
    # A tabela detalhada não muda com o filtro de ticker.
    assert [a.ticker for a in rel.ativos] == ["AAAA3", "BBBB3"]
    aaaa = next(a for a in rel.ativos if a.ticker == "AAAA3")
    assert aaaa.yoc_total == Decimal("10.0000")
    assert aaaa.valor_recebido_total == Decimal("100.00")


def test_filtro_so_por_periodo_afeta_total_do_consolidado_e_dos_ativos():
    ativos, proventos = _cenario_multi()
    rel = calcular_relatorio_yoc(
        ativos, proventos, HOJE,
        data_inicio=date(2024, 1, 1), data_fim=date(2024, 12, 31),
    )
    cons = rel.consolidado
    # Só eventos de 2024: 4 (AAAA3) + 5 (BBBB3) = 9%.
    assert cons.valor_recebido_total == Decimal("90.00")
    assert cons.yoc_total == Decimal("9.0000")

    # O recorte de datas também recorta a coluna `total` de cada ativo.
    aaaa = next(a for a in rel.ativos if a.ticker == "AAAA3")
    assert aaaa.yoc_total == Decimal("4.0000")  # só o evento de 2024 (não 4+6)
    assert aaaa.valor_recebido_total == Decimal("40.00")
    # A janela de 12m do ativo não é afetada pelo recorte.
    assert aaaa.yoc_12m == Decimal("4.0000")


def test_filtro_por_ticker_e_periodo_juntos():
    ativos, proventos = _cenario_multi()
    rel = calcular_relatorio_yoc(
        ativos, proventos, HOJE,
        ticker="AAAA3", data_inicio=date(2024, 1, 1), data_fim=date(2024, 12, 31),
    )
    cons = rel.consolidado
    # AAAA3 em 2024: só o evento de março → 4%.
    assert cons.valor_recebido_total == Decimal("40.00")
    assert cons.yoc_total == Decimal("4.0000")


def test_filtro_sem_nenhum_provento_no_recorte_retorna_null():
    ativos, proventos = _cenario_multi()
    rel = calcular_relatorio_yoc(
        ativos, proventos, HOJE,
        data_inicio=date(2025, 1, 1), data_fim=date(2025, 12, 31),
    )
    cons = rel.consolidado
    assert cons.valor_recebido_total == Decimal("0.00")
    assert cons.yoc_total is None
    # 12m segue reportado normalmente (janela fixa, sem filtro).
    assert cons.yoc_12m == Decimal("9.0000")


def test_filtro_por_ticker_alcanca_ativo_sem_posicao_aberta():
    # ZZZZ3 tem proventos, mas NÃO está em `ativos` (posição vendida/zerada).
    # Com filtro de ticker, o consolidado deve alcançá-lo mesmo assim.
    ativos = [_ativo("AAAA3", 100, 10)]  # só AAAA3 tem posição aberta
    proventos = {
        "AAAA3": [_prov("AAAA3", recebido=40, yoc="4.0000", data_pag=date(2024, 3, 1))],
        "ZZZZ3": [
            _prov("ZZZZ3", recebido=70, yoc="7.0000", data_pag=date(2024, 4, 1)),
            _prov("ZZZZ3", recebido=30, yoc="3.0000", data_pag=date(2023, 1, 1)),
        ],
    }

    rel = calcular_relatorio_yoc(ativos, proventos, HOJE, ticker="ZZZZ3")
    cons = rel.consolidado

    # ZZZZ3: 7 + 3 = 10%, mesmo sem posição aberta.
    assert cons.valor_recebido_total == Decimal("100.00")
    assert cons.yoc_total == Decimal("10.0000")
    # A tabela continua só com os ativos de posição aberta.
    assert [a.ticker for a in rel.ativos] == ["AAAA3"]


def test_sem_ticker_consolidado_ignora_ativos_sem_posicao_aberta():
    # Sem filtro de ticker, o consolidado representa a carteira ATUAL: os
    # proventos de ZZZZ3 (sem posição aberta) não entram.
    ativos = [_ativo("AAAA3", 100, 10)]
    proventos = {
        "AAAA3": [_prov("AAAA3", recebido=40, yoc="4.0000", data_pag=date(2024, 3, 1))],
        "ZZZZ3": [_prov("ZZZZ3", recebido=70, yoc="7.0000", data_pag=date(2024, 4, 1))],
    }

    rel = calcular_relatorio_yoc(ativos, proventos, HOJE)
    cons = rel.consolidado

    # Só AAAA3: 4%.
    assert cons.valor_recebido_total == Decimal("40.00")
    assert cons.yoc_total == Decimal("4.0000")
