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


def _prov(ticker, valor_recebido, quantidade, pm, data_pag=None, data_com=None, tipo="Dividendo"):
    """Provento já persistido (com os campos calculados na criação). O
    valor_recebido é o BRUTO — a retenção de IR (JCP) é aplicada no relatório."""
    return Provento(
        ticker=ticker,
        tipo_provento=tipo,
        data_com=data_com,
        data_pagamento=data_pag,
        valor_por_acao=Decimal("0"),
        quantidade=Decimal(str(quantidade)) if quantidade is not None else None,
        pm_historico=Decimal(str(pm)) if pm is not None else None,
        valor_recebido=Decimal(str(valor_recebido)) if valor_recebido is not None else None,
        yoc_evento=None,
    )


def test_provento_de_ciclo_antigo_usa_pm_persistido_nao_o_atual():
    # Posição atual do WEGE3: PM 20 (ciclo novo). Mas há um provento de um ciclo
    # antigo, criado quando o PM era 10 — o YoC deve usar esse PM 10 persistido.
    ativos = [_ativo("WEGE3", 100, 20)]
    proventos = {
        "WEGE3": [_prov("WEGE3", valor_recebido=50, quantidade=100, pm=10, data_pag=date(2024, 3, 1))],
    }

    rel = calcular_relatorio_yoc(ativos, proventos, HOJE)
    ativo = rel.ativos[0]

    # PM atual é exibido (20), mas o custo-base do evento é 100 × 10 = 1000.
    assert ativo.pm_historico_atual == Decimal(20)
    assert ativo.valor_recebido_total == Decimal("50.00")
    assert ativo.yoc_total == Decimal("5.0000")  # 50 / 1000 × 100 (não 50/2000)
    assert ativo.yoc_12m == Decimal("5.0000")


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
            _prov("BBAS3", valor_recebido=60, quantidade=100, pm=10, data_pag=date(2024, 3, 1)),  # 12m
            _prov("BBAS3", valor_recebido=50, quantidade=100, pm=10, data_pag=date(2022, 1, 1)),  # fora
        ],
    }

    rel = calcular_relatorio_yoc(ativos, proventos, HOJE)
    ativo = rel.ativos[0]

    # 12m: 60 recebido / 1000 base → 6%.
    assert ativo.valor_recebido_12m == Decimal("60.00")
    assert ativo.yoc_12m == Decimal("6.0000")
    # Total: 110 recebido / 2000 base → 5,5%.
    assert ativo.valor_recebido_total == Decimal("110.00")
    assert ativo.yoc_total == Decimal("5.5000")


def test_usa_data_com_quando_pagamento_e_nulo():
    # Sem data_pagamento, a janela de 12m usa a data_com.
    ativos = [_ativo("ITUB4", 100, 10)]
    proventos = {
        "ITUB4": [_prov("ITUB4", valor_recebido=40, quantidade=100, pm=10, data_com=date(2024, 2, 1))],
    }

    rel = calcular_relatorio_yoc(ativos, proventos, HOJE)
    ativo = rel.ativos[0]

    assert ativo.valor_recebido_12m == Decimal("40.00")
    assert ativo.yoc_12m == Decimal("4.0000")


def test_consolidado_pondera_por_evento_nao_media_simples():
    # A: custo-base 100×10 = 1000, recebe 100 → YoC 10%.
    # B: custo-base 100×50 = 5000, recebe 100 → YoC 2%.
    # Média simples seria (10 + 2)/2 = 6%. Ponderado por evento:
    #   (100 + 100) / (1000 + 5000) × 100 = 3,3333%.
    ativos = [_ativo("AAAA3", 100, 10), _ativo("BBBB3", 100, 50)]
    proventos = {
        "AAAA3": [_prov("AAAA3", valor_recebido=100, quantidade=100, pm=10, data_pag=date(2024, 1, 15))],
        "BBBB3": [_prov("BBBB3", valor_recebido=100, quantidade=100, pm=50, data_pag=date(2024, 1, 15))],
    }

    rel = calcular_relatorio_yoc(ativos, proventos, HOJE)
    por_ticker = {a.ticker: a for a in rel.ativos}

    assert por_ticker["AAAA3"].yoc_total == Decimal("10.0000")
    assert por_ticker["BBBB3"].yoc_total == Decimal("2.0000")

    cons = rel.consolidado
    assert cons.valor_recebido_total == Decimal("200.00")
    assert cons.yoc_total == Decimal("3.3333")  # ponderado, não 6%
    assert cons.yoc_12m == Decimal("3.3333")
    assert cons.yoc_total != Decimal("6.0000")


# ── Retenção de IR do JCP no relatório ───────────────────────────────────────


def test_jcp_entra_liquido_no_ativo_e_no_consolidado():
    # JCP retém 17,5%: bruto 100 → líquido 82,50. base 100×10 = 1000 → YoC 8,25%.
    ativos = [_ativo("TAEE11", 100, 10)]
    proventos = {
        "TAEE11": [
            _prov("TAEE11", valor_recebido=100, quantidade=100, pm=10, data_pag=date(2024, 3, 1), tipo="JCP"),
        ],
    }

    rel = calcular_relatorio_yoc(ativos, proventos, HOJE)
    ativo = rel.ativos[0]

    assert ativo.valor_recebido_total == Decimal("82.50")
    assert ativo.yoc_total == Decimal("8.2500")
    assert ativo.valor_recebido_12m == Decimal("82.50")
    assert ativo.yoc_12m == Decimal("8.2500")
    # Ano-calendário atual também líquido.
    assert ativo.valor_recebido_ano == Decimal("82.50")
    assert ativo.yoc_ano == Decimal("8.2500")
    # Consolidado não diverge da listagem (que já é líquida).
    assert rel.consolidado.valor_recebido_total == Decimal("82.50")
    assert rel.consolidado.yoc_total == Decimal("8.2500")


def test_consolidado_neta_apenas_jcp_nao_dividendo():
    # A (JCP): bruto 100 → líquido 82,50. B (Dividendo, isento): 100.
    # Consolidado: (82,50 + 100) / (1000 + 1000) × 100 = 9,125%.
    ativos = [_ativo("AAAA3", 100, 10), _ativo("BBBB3", 100, 10)]
    proventos = {
        "AAAA3": [_prov("AAAA3", 100, 100, 10, data_pag=date(2024, 1, 15), tipo="JCP")],
        "BBBB3": [_prov("BBBB3", 100, 100, 10, data_pag=date(2024, 1, 15), tipo="Dividendo")],
    }

    rel = calcular_relatorio_yoc(ativos, proventos, HOJE)
    por_ticker = {a.ticker: a for a in rel.ativos}

    assert por_ticker["AAAA3"].valor_recebido_total == Decimal("82.50")  # JCP líquido
    assert por_ticker["BBBB3"].valor_recebido_total == Decimal("100.00")  # dividendo bruto=líquido

    cons = rel.consolidado
    assert cons.valor_recebido_total == Decimal("182.50")
    assert cons.yoc_total == Decimal("9.1250")


# ── Campos do ano-calendário atual ───────────────────────────────────────────


def test_valor_recebido_ano_e_yoc_ano_do_ano_calendario():
    # HOJE = 2024-07-01 → ano-calendário 2024.
    ativos = [_ativo("BBAS3", 100, 10)]
    proventos = {
        "BBAS3": [
            _prov("BBAS3", 40, 100, 10, data_pag=date(2024, 3, 1)),  # 2024: ano + 12m
            _prov("BBAS3", 30, 100, 10, data_pag=date(2023, 8, 1)),  # 12m, mas ano anterior
        ],
    }

    rel = calcular_relatorio_yoc(ativos, proventos, HOJE)
    ativo = rel.ativos[0]

    # Ano 2024: só o evento de março. 40 / 1000 → 4%.
    assert ativo.valor_recebido_ano == Decimal("40.00")
    assert ativo.yoc_ano == Decimal("4.0000")
    # 12m pega os dois: 70 / 2000 → 3,5%.
    assert ativo.valor_recebido_12m == Decimal("70.00")
    assert ativo.yoc_12m == Decimal("3.5000")


def test_yoc_ano_null_quando_nao_recebeu_no_ano():
    # Único provento é de ano anterior → nada no ano-calendário atual.
    ativos = [_ativo("EGIE3", 100, 10)]
    proventos = {
        "EGIE3": [_prov("EGIE3", 50, 100, 10, data_pag=date(2023, 5, 1))],
    }

    rel = calcular_relatorio_yoc(ativos, proventos, HOJE)
    ativo = rel.ativos[0]

    assert ativo.valor_recebido_ano == Decimal("0.00")
    assert ativo.yoc_ano is None  # None (não 0) = sem recebimento no ano
    # Ainda contribui no acumulado total.
    assert ativo.valor_recebido_total == Decimal("50.00")
