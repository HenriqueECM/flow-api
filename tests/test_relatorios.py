"""Relatório de YoC: consolidação, filtros e isolamento.

O motor já tem cobertura unitária extensa (test_relatorios_engine.py). O que
falta e é testado aqui é a **fiação**: o router lê transações e proventos do
banco, calcula as posições abertas, agrupa por ticker e repassa os filtros. Um
erro nessa costura — agrupar errado, esquecer de repassar um filtro, montar a
lista de ativos sem filtrar por carteira — não é pego por nenhum teste de motor.

Sobre as datas: o router usa `date.today()` para a janela de 12 meses e para o
ano-calendário, então as constantes abaixo são derivadas de hoje. Datas fixas
(2024, por exemplo) sairiam da janela com o passar do tempo e o teste passaria a
falhar sozinho.
"""

from datetime import date
from decimal import Decimal
from uuid import UUID, uuid4

from sqlalchemy import func, select

from app.models import Carteira, Provento, Transacao

OUTRO_USER_ID = UUID("00000000-0000-0000-0000-0000000000bb")

HOJE = date.today()

# 1º de janeiro deste ano: sempre dentro dos últimos 12 meses (o corte é o mesmo
# dia do ano passado) e sempre no ano-calendário atual. Vale em qualquer data.
DENTRO_12M_E_ANO = date(HOJE.year, 1, 1)
# 31 de dezembro do ano passado: dentro dos 12 meses, fora do ano atual — é o
# que distingue as duas janelas.
DENTRO_12M_ANO_ANTERIOR = date(HOJE.year - 1, 12, 31)
# Dois anos atrás: fora das duas janelas.
FORA_12M = date(HOJE.year - 2, 6, 1)
# Compra antiga o bastante para a posição estar aberta em qualquer "hoje".
COMPRA = date(HOJE.year - 3, 1, 2)


async def _carteira(db_session, user_id, nome="Carteira"):
    carteira = Carteira(user_id=user_id, nome=nome)
    db_session.add(carteira)
    await db_session.commit()
    return carteira.id


def _tx(carteira_id, ticker, operacao, qtd, preco, dia=COMPRA, nome=None):
    return Transacao(
        carteira_id=carteira_id,
        ticker=ticker,
        nome=nome,
        operacao=operacao,
        quantidade=Decimal(str(qtd)),
        preco_unit=Decimal(str(preco)),
        data=dia,
    )


def _prov(carteira_id, ticker, recebido, yoc, pago_em, tipo="Dividendo"):
    return Provento(
        carteira_id=carteira_id,
        ticker=ticker,
        tipo_provento=tipo,
        data_com=pago_em,
        data_pagamento=pago_em,
        valor_por_acao=Decimal("0.50"),
        quantidade=Decimal("100"),
        pm_historico=Decimal("10.0000"),
        valor_recebido=Decimal(str(recebido)),  # bruto
        yoc_evento=Decimal(str(yoc)),  # bruto
    )


async def test_relatorio_consolida_proventos_por_janela(
    client, usuario_autenticado, db_session, override_get_db
):
    carteira_id = await _carteira(db_session, usuario_autenticado.id)
    db_session.add(_tx(carteira_id, "PETR4", "compra", 100, "10.00", nome="Petrobras"))
    db_session.add_all(
        [
            _prov(carteira_id, "PETR4", "50.00", "5.0000", DENTRO_12M_E_ANO),
            _prov(carteira_id, "PETR4", "30.00", "3.0000", FORA_12M),
        ]
    )
    await db_session.commit()

    resposta = await client.get(f"/carteiras/{carteira_id}/relatorios/yoc")

    assert resposta.status_code == 200, resposta.text
    corpo = resposta.json()
    assert len(corpo["ativos"]) == 1
    ativo = corpo["ativos"][0]

    assert ativo["ticker"] == "PETR4"
    # Nome vem da transação mais recente que tiver um; cai para o ticker se não.
    assert ativo["nome"] == "Petrobras"
    assert Decimal(ativo["quantidade_atual"]) == Decimal("100")
    assert Decimal(ativo["pm_historico_atual"]) == Decimal("10")

    # As três janelas separam os mesmos dois eventos de formas diferentes.
    assert Decimal(ativo["valor_recebido_12m"]) == Decimal("50.00")
    assert Decimal(ativo["valor_recebido_ano"]) == Decimal("50.00")
    assert Decimal(ativo["valor_recebido_total"]) == Decimal("80.00")
    assert Decimal(ativo["yoc_12m"]) == Decimal("5")
    assert Decimal(ativo["yoc_total"]) == Decimal("8")

    consolidado = corpo["consolidado"]
    assert Decimal(consolidado["valor_recebido_12m"]) == Decimal("50.00")
    assert Decimal(consolidado["valor_recebido_total"]) == Decimal("80.00")


async def test_janela_de_12m_e_de_ano_nao_sao_a_mesma_coisa(
    client, usuario_autenticado, db_session, override_get_db
):
    carteira_id = await _carteira(db_session, usuario_autenticado.id)
    db_session.add(_tx(carteira_id, "PETR4", "compra", 100, "10.00"))
    # 31/12 do ano passado: conta nos 12 meses, não conta no ano-calendário.
    db_session.add(
        _prov(carteira_id, "PETR4", "40.00", "4.0000", DENTRO_12M_ANO_ANTERIOR)
    )
    await db_session.commit()

    resposta = await client.get(f"/carteiras/{carteira_id}/relatorios/yoc")

    assert resposta.status_code == 200, resposta.text
    ativo = resposta.json()["ativos"][0]
    assert Decimal(ativo["valor_recebido_12m"]) == Decimal("40.00")
    assert Decimal(ativo["valor_recebido_ano"]) == Decimal("0.00")
    # Sem evento no ano, o YoC do ano é nulo — "sem dado", não "rendeu zero".
    assert ativo["yoc_ano"] is None


async def test_ativo_vendido_nao_aparece_na_tabela(
    client, usuario_autenticado, db_session, override_get_db
):
    carteira_id = await _carteira(db_session, usuario_autenticado.id)
    db_session.add_all(
        [
            _tx(carteira_id, "PETR4", "compra", 100, "10.00"),
            # Ciclo encerrado: a posição zerou.
            _tx(carteira_id, "XPTO3", "compra", 50, "20.00"),
            _tx(
                carteira_id,
                "XPTO3",
                "venda",
                50,
                "25.00",
                dia=date(HOJE.year - 2, 1, 5),
            ),
        ]
    )
    db_session.add(_prov(carteira_id, "XPTO3", "10.00", "1.0000", DENTRO_12M_E_ANO))
    await db_session.commit()

    resposta = await client.get(f"/carteiras/{carteira_id}/relatorios/yoc")

    assert resposta.status_code == 200, resposta.text
    corpo = resposta.json()
    # A tabela é da carteira ATUAL: quem foi vendido sai.
    assert [a["ticker"] for a in corpo["ativos"]] == ["PETR4"]
    # E o consolidado sem filtro só olha os ativos abertos, então o provento do
    # vendido não entra.
    assert Decimal(corpo["consolidado"]["valor_recebido_total"]) == Decimal("0.00")


async def test_filtro_por_ticker_afeta_o_consolidado_e_nao_a_tabela(
    client, usuario_autenticado, db_session, override_get_db
):
    carteira_id = await _carteira(db_session, usuario_autenticado.id)
    db_session.add_all(
        [
            _tx(carteira_id, "PETR4", "compra", 100, "10.00"),
            _tx(carteira_id, "VALE3", "compra", 30, "60.00"),
        ]
    )
    db_session.add_all(
        [
            _prov(carteira_id, "PETR4", "50.00", "5.0000", DENTRO_12M_E_ANO),
            _prov(carteira_id, "VALE3", "30.00", "3.0000", DENTRO_12M_E_ANO),
        ]
    )
    await db_session.commit()

    resposta = await client.get(
        f"/carteiras/{carteira_id}/relatorios/yoc", params={"ticker": "PETR4"}
    )

    assert resposta.status_code == 200, resposta.text
    corpo = resposta.json()
    # A tabela continua completa: o filtro é do KPI, não da listagem.
    assert [a["ticker"] for a in corpo["ativos"]] == ["PETR4", "VALE3"]
    # Só o total do consolidado é recortado...
    assert Decimal(corpo["consolidado"]["valor_recebido_total"]) == Decimal("50.00")
    # ...e os 12m têm janela fixa, sem filtro.
    assert Decimal(corpo["consolidado"]["valor_recebido_12m"]) == Decimal("80.00")


async def test_filtro_por_periodo_recorta_o_total(
    client, usuario_autenticado, db_session, override_get_db
):
    carteira_id = await _carteira(db_session, usuario_autenticado.id)
    db_session.add(_tx(carteira_id, "PETR4", "compra", 100, "10.00"))
    db_session.add_all(
        [
            _prov(carteira_id, "PETR4", "50.00", "5.0000", DENTRO_12M_E_ANO),
            _prov(carteira_id, "PETR4", "30.00", "3.0000", FORA_12M),
        ]
    )
    await db_session.commit()

    resposta = await client.get(
        f"/carteiras/{carteira_id}/relatorios/yoc",
        params={"data_inicio": DENTRO_12M_E_ANO.isoformat()},
    )

    assert resposta.status_code == 200, resposta.text
    corpo = resposta.json()
    # Só o evento dentro do recorte entra no total; o antigo sai.
    assert Decimal(corpo["ativos"][0]["valor_recebido_total"]) == Decimal("50.00")
    assert Decimal(corpo["consolidado"]["valor_recebido_total"]) == Decimal("50.00")


async def test_jcp_entra_liquido_no_relatorio(
    client, usuario_autenticado, db_session, override_get_db
):
    carteira_id = await _carteira(db_session, usuario_autenticado.id)
    db_session.add(_tx(carteira_id, "PETR4", "compra", 100, "10.00"))
    # Gravado bruto, como o endpoint de proventos faz.
    db_session.add(
        _prov(carteira_id, "PETR4", "50.00", "5.0000", DENTRO_12M_E_ANO, tipo="JCP")
    )
    await db_session.commit()

    resposta = await client.get(f"/carteiras/{carteira_id}/relatorios/yoc")

    assert resposta.status_code == 200, resposta.text
    ativo = resposta.json()["ativos"][0]
    # 50,00 x 0,825. Um relatório somando o bruto exageraria o que o usuário
    # de fato recebeu.
    assert Decimal(ativo["valor_recebido_12m"]) == Decimal("41.25")
    assert Decimal(ativo["yoc_12m"]) == Decimal("4.1250")


async def test_carteira_sem_dados_devolve_relatorio_vazio(
    client, usuario_autenticado, db_session, override_get_db
):
    carteira_id = await _carteira(db_session, usuario_autenticado.id)

    resposta = await client.get(f"/carteiras/{carteira_id}/relatorios/yoc")

    assert resposta.status_code == 200, resposta.text
    corpo = resposta.json()
    assert corpo["ativos"] == []
    assert Decimal(corpo["consolidado"]["valor_recebido_total"]) == Decimal("0.00")
    assert corpo["consolidado"]["yoc_total"] is None


async def test_relatorio_nao_mistura_dados_de_outra_carteira_do_mesmo_usuario(
    client, usuario_autenticado, db_session, override_get_db
):
    primeira = await _carteira(db_session, usuario_autenticado.id, nome="Primeira")
    segunda = await _carteira(db_session, usuario_autenticado.id, nome="Segunda")
    db_session.add_all(
        [
            _tx(primeira, "PETR4", "compra", 100, "10.00"),
            _tx(segunda, "VALE3", "compra", 30, "60.00"),
        ]
    )
    db_session.add_all(
        [
            _prov(primeira, "PETR4", "50.00", "5.0000", DENTRO_12M_E_ANO),
            _prov(segunda, "VALE3", "30.00", "3.0000", DENTRO_12M_E_ANO),
        ]
    )
    await db_session.commit()

    resposta = await client.get(f"/carteiras/{primeira}/relatorios/yoc")

    assert resposta.status_code == 200, resposta.text
    corpo = resposta.json()
    assert [a["ticker"] for a in corpo["ativos"]] == ["PETR4"]
    assert Decimal(corpo["consolidado"]["valor_recebido_12m"]) == Decimal("50.00")
    assert "VALE3" not in resposta.text


async def test_carteira_de_outro_usuario_nao_expoe_relatorio(
    client, usuario_autenticado, db_session, override_get_db
):
    assert OUTRO_USER_ID != usuario_autenticado.id

    alheia_id = await _carteira(db_session, OUTRO_USER_ID, nome="Carteira alheia")
    db_session.add(_tx(alheia_id, "PETR4", "compra", 100, "10.00"))
    db_session.add(_prov(alheia_id, "PETR4", "50.00", "5.0000", DENTRO_12M_E_ANO))
    await db_session.commit()

    # Prova que há o que vazar antes de exigir que não vaze.
    assert (
        await db_session.scalar(
            select(func.count())
            .select_from(Provento)
            .where(Provento.carteira_id == alheia_id)
        )
        == 1
    )

    resposta = await client.get(f"/carteiras/{alheia_id}/relatorios/yoc")

    assert resposta.status_code == 404, resposta.text
    assert "PETR4" not in resposta.text


async def test_carteira_inexistente_responde_404(
    client, usuario_autenticado, db_session, override_get_db
):
    resposta = await client.get(f"/carteiras/{uuid4()}/relatorios/yoc")

    assert resposta.status_code == 404, resposta.text
