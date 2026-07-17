"""Proventos: criação, leitura, preview e retenção de IR.

Duas regras deste módulo não aparecem na resposta HTTP e por isso são
verificadas no banco:

1. **Persiste bruto, devolve líquido.** O valor gravado é o fato imutável
   (quantidade × valor por ação); a retenção de 17,5% do JCP é derivada na
   leitura. Um teste que só olhasse a resposta não distinguiria "gravou bruto e
   liquidou na saída" de "gravou líquido e devolveu como está" — e a segunda
   opção aplicaria o imposto duas vezes na próxima leitura.

2. **Os campos calculados vêm da posição na Data COM**, não de hoje. Quem
   recebeu provento tinha uma quantidade e um PM naquela data, e é sobre eles
   que o YoC é medido.
"""

from datetime import date, datetime, timezone
from decimal import Decimal
from uuid import UUID, uuid4

from sqlalchemy import func, select

from app.models import Carteira, Provento, Transacao

OUTRO_USER_ID = UUID("00000000-0000-0000-0000-0000000000bb")


async def _carteira(db_session, user_id, nome="Carteira"):
    carteira = Carteira(user_id=user_id, nome=nome)
    db_session.add(carteira)
    await db_session.commit()
    return carteira.id


def _tx(carteira_id, ticker, operacao, qtd, preco, dia):
    return Transacao(
        carteira_id=carteira_id,
        ticker=ticker,
        operacao=operacao,
        quantidade=Decimal(str(qtd)),
        preco_unit=Decimal(str(preco)),
        data=dia,
    )


async def _linha_provento(db_session):
    """Colunas cruas do único provento — o que está no Postgres, não o objeto
    do identity map que o endpoint acabou de criar."""
    return (
        await db_session.execute(
            select(
                Provento.id,
                Provento.carteira_id,
                Provento.ticker,
                Provento.tipo_provento,
                Provento.data_com,
                Provento.data_pagamento,
                Provento.valor_por_acao,
                Provento.quantidade,
                Provento.pm_historico,
                Provento.valor_recebido,
                Provento.yoc_evento,
            )
        )
    ).one()


async def test_cria_provento_calculando_a_posicao_na_data_com(
    client, usuario_autenticado, db_session, override_get_db
):
    carteira_id = await _carteira(db_session, usuario_autenticado.id)
    db_session.add(_tx(carteira_id, "PETR4", "compra", 100, "10.00", date(2024, 1, 10)))
    await db_session.commit()

    resposta = await client.post(
        f"/carteiras/{carteira_id}/proventos",
        json={
            "ticker": "PETR4",
            "tipo_provento": "Dividendo",
            "data_com": "2024-06-01",
            "data_pagamento": "2024-06-20",
            "valor_por_acao": "0.50",
        },
    )

    assert resposta.status_code == 201, resposta.text
    corpo = resposta.json()
    assert corpo["carteira_id"] == str(carteira_id)
    assert Decimal(corpo["quantidade"]) == Decimal("100")
    assert Decimal(corpo["pm_historico"]) == Decimal("10")
    # 100 ações x R$ 0,50.
    assert Decimal(corpo["valor_recebido"]) == Decimal("50.00")
    # (0,50 / 10,00) x 100 = 5% sobre o custo.
    assert Decimal(corpo["yoc_evento"]) == Decimal("5")

    linha = await _linha_provento(db_session)
    assert linha.carteira_id == carteira_id
    assert linha.data_com == date(2024, 6, 1)
    assert linha.data_pagamento == date(2024, 6, 20)
    assert linha.valor_por_acao == Decimal("0.50")
    # Dividendo é isento: gravado e devolvido são o mesmo número.
    assert linha.valor_recebido == Decimal("50.00")
    assert linha.yoc_evento == Decimal("5")


async def test_jcp_persiste_bruto_e_devolve_liquido(
    client, usuario_autenticado, db_session, override_get_db
):
    carteira_id = await _carteira(db_session, usuario_autenticado.id)
    db_session.add(_tx(carteira_id, "PETR4", "compra", 100, "10.00", date(2024, 1, 10)))
    await db_session.commit()

    resposta = await client.post(
        f"/carteiras/{carteira_id}/proventos",
        json={
            "ticker": "PETR4",
            "tipo_provento": "JCP",
            "data_com": "2024-06-01",
            "valor_por_acao": "0.50",
        },
    )

    assert resposta.status_code == 201, resposta.text
    corpo = resposta.json()
    # A resposta traz o líquido: 50,00 x 0,825 = 41,25 (17,5% de IRRF).
    assert Decimal(corpo["valor_recebido"]) == Decimal("41.25")
    assert Decimal(corpo["yoc_evento"]) == Decimal("4.1250")

    # E o banco guarda o BRUTO. É a asserção central do módulo: se a gravação
    # fosse líquida, a próxima leitura aplicaria o imposto de novo (41,25 x
    # 0,825 = 34,03) e o valor encolheria a cada request.
    linha = await _linha_provento(db_session)
    assert linha.valor_recebido == Decimal("50.00")
    assert linha.yoc_evento == Decimal("5")


async def test_provento_sem_posicao_na_data_com_e_criado_com_campos_nulos(
    client, usuario_autenticado, db_session, override_get_db
):
    carteira_id = await _carteira(db_session, usuario_autenticado.id)
    # A compra é POSTERIOR à Data COM: na data do evento não havia posição.
    db_session.add(_tx(carteira_id, "PETR4", "compra", 100, "10.00", date(2024, 6, 10)))
    await db_session.commit()

    resposta = await client.post(
        f"/carteiras/{carteira_id}/proventos",
        json={
            "ticker": "PETR4",
            "tipo_provento": "Dividendo",
            "data_com": "2024-01-05",
            "valor_por_acao": "0.50",
        },
    )

    # Criar mesmo assim é o contrato: o usuário pode registrar o provento antes
    # de importar as transações. Nulo (e não zero) diz "sem dado", não "recebeu
    # zero".
    assert resposta.status_code == 201, resposta.text
    corpo = resposta.json()
    assert corpo["quantidade"] is None
    assert corpo["pm_historico"] is None
    assert corpo["valor_recebido"] is None
    assert corpo["yoc_evento"] is None

    linha = await _linha_provento(db_session)
    assert linha.valor_por_acao == Decimal("0.50")
    assert linha.quantidade is None
    assert linha.valor_recebido is None


async def test_lista_devolve_o_liquido_a_partir_do_bruto_gravado(
    client, usuario_autenticado, db_session, override_get_db
):
    carteira_id = await _carteira(db_session, usuario_autenticado.id)
    db_session.add_all(
        [
            Provento(
                carteira_id=carteira_id,
                ticker="PETR4",
                tipo_provento="JCP",
                data_com=date(2024, 5, 1),
                data_pagamento=date(2024, 5, 20),
                valor_por_acao=Decimal("0.50"),
                quantidade=Decimal("100"),
                pm_historico=Decimal("10.0000"),
                valor_recebido=Decimal("50.00"),  # bruto
                yoc_evento=Decimal("5.0000"),  # bruto
            ),
            Provento(
                carteira_id=carteira_id,
                ticker="VALE3",
                tipo_provento="Dividendo",
                data_com=date(2024, 8, 1),
                data_pagamento=date(2024, 8, 15),
                valor_por_acao=Decimal("1.00"),
                quantidade=Decimal("30"),
                pm_historico=Decimal("60.0000"),
                valor_recebido=Decimal("30.00"),
                yoc_evento=Decimal("1.6667"),
            ),
        ]
    )
    await db_session.commit()

    resposta = await client.get(f"/carteiras/{carteira_id}/proventos")

    assert resposta.status_code == 200, resposta.text
    corpo = resposta.json()
    # Mais recente primeiro (data_pagamento desc).
    assert [p["ticker"] for p in corpo] == ["VALE3", "PETR4"]

    dividendo, jcp = corpo
    # Isento passa inalterado...
    assert Decimal(dividendo["valor_recebido"]) == Decimal("30.00")
    # ...e o JCP é liquidado na leitura, sem tocar no que está gravado.
    assert Decimal(jcp["valor_recebido"]) == Decimal("41.25")
    assert Decimal(jcp["yoc_evento"]) == Decimal("4.1250")


async def test_preview_calcula_sem_persistir(
    client, usuario_autenticado, db_session, override_get_db
):
    carteira_id = await _carteira(db_session, usuario_autenticado.id)
    db_session.add(_tx(carteira_id, "PETR4", "compra", 100, "10.00", date(2024, 1, 10)))
    await db_session.commit()

    resposta = await client.get(
        f"/carteiras/{carteira_id}/proventos/preview",
        params={
            "ticker": "PETR4",
            "data_com": "2024-06-01",
            "valor_por_acao": "0.50",
            "tipo_provento": "JCP",
        },
    )

    assert resposta.status_code == 200, resposta.text
    corpo = resposta.json()
    assert Decimal(corpo["quantidade"]) == Decimal("100")
    assert Decimal(corpo["pm_historico"]) == Decimal("10")
    # Informar o tipo faz o preview já mostrar o líquido — coerente com o que a
    # listagem exibirá depois de salvar.
    assert Decimal(corpo["valor_recebido"]) == Decimal("41.25")
    assert Decimal(corpo["yoc_evento"]) == Decimal("4.1250")

    # É um preview: nada pode ter sido gravado.
    assert await db_session.scalar(select(func.count()).select_from(Provento)) == 0


async def test_preview_sem_tipo_devolve_o_bruto(
    client, usuario_autenticado, db_session, override_get_db
):
    carteira_id = await _carteira(db_session, usuario_autenticado.id)
    db_session.add(_tx(carteira_id, "PETR4", "compra", 100, "10.00", date(2024, 1, 10)))
    await db_session.commit()

    resposta = await client.get(
        f"/carteiras/{carteira_id}/proventos/preview",
        params={"ticker": "PETR4", "data_com": "2024-06-01", "valor_por_acao": "0.50"},
    )

    assert resposta.status_code == 200, resposta.text
    assert Decimal(resposta.json()["valor_recebido"]) == Decimal("50.00")


async def test_preview_sem_valor_por_acao_traz_posicao_e_anula_o_resto(
    client, usuario_autenticado, db_session, override_get_db
):
    carteira_id = await _carteira(db_session, usuario_autenticado.id)
    db_session.add(_tx(carteira_id, "PETR4", "compra", 100, "10.00", date(2024, 1, 10)))
    await db_session.commit()

    resposta = await client.get(
        f"/carteiras/{carteira_id}/proventos/preview",
        params={"ticker": "PETR4", "data_com": "2024-06-01"},
    )

    assert resposta.status_code == 200, resposta.text
    corpo = resposta.json()
    # O formulário mostra quantidade e PM enquanto o usuário ainda não digitou
    # o valor por ação.
    assert Decimal(corpo["quantidade"]) == Decimal("100")
    assert Decimal(corpo["pm_historico"]) == Decimal("10")
    assert corpo["valor_recebido"] is None
    assert corpo["yoc_evento"] is None


async def test_preview_incompleto_devolve_nulos_e_nao_erro(
    client, usuario_autenticado, db_session, override_get_db
):
    carteira_id = await _carteira(db_session, usuario_autenticado.id)
    vazio = {
        "quantidade": None,
        "pm_historico": None,
        "valor_recebido": None,
        "yoc_evento": None,
    }

    # É chamado a cada tecla digitada: parâmetros faltando são estado normal do
    # formulário, não erro de cliente.
    sem_nada = await client.get(f"/carteiras/{carteira_id}/proventos/preview")
    sem_data = await client.get(
        f"/carteiras/{carteira_id}/proventos/preview", params={"ticker": "PETR4"}
    )
    sem_ticker = await client.get(
        f"/carteiras/{carteira_id}/proventos/preview", params={"data_com": "2024-06-01"}
    )

    for resposta in (sem_nada, sem_data, sem_ticker):
        assert resposta.status_code == 200, resposta.text
        assert resposta.json() == vazio


async def test_lista_nao_mistura_proventos_de_outra_carteira_do_mesmo_usuario(
    client, usuario_autenticado, db_session, override_get_db
):
    primeira = await _carteira(db_session, usuario_autenticado.id, nome="Primeira")
    segunda = await _carteira(db_session, usuario_autenticado.id, nome="Segunda")
    db_session.add_all(
        [
            Provento(
                carteira_id=primeira,
                ticker="PETR4",
                tipo_provento="Dividendo",
                data_com=date(2024, 5, 1),
                data_pagamento=date(2024, 5, 20),
                valor_por_acao=Decimal("0.50"),
                created_at=datetime(2024, 5, 20, tzinfo=timezone.utc),
            ),
            Provento(
                carteira_id=segunda,
                ticker="VALE3",
                tipo_provento="Dividendo",
                data_com=date(2024, 6, 1),
                data_pagamento=date(2024, 6, 20),
                valor_por_acao=Decimal("1.00"),
                created_at=datetime(2024, 6, 20, tzinfo=timezone.utc),
            ),
        ]
    )
    await db_session.commit()

    resposta = await client.get(f"/carteiras/{primeira}/proventos")

    assert resposta.status_code == 200, resposta.text
    corpo = resposta.json()
    assert [p["ticker"] for p in corpo] == ["PETR4"]
    assert "VALE3" not in resposta.text


async def test_carteira_de_outro_usuario_nao_expoe_nem_aceita_proventos(
    client, usuario_autenticado, db_session, override_get_db
):
    assert OUTRO_USER_ID != usuario_autenticado.id

    alheia_id = await _carteira(db_session, OUTRO_USER_ID, nome="Carteira alheia")
    db_session.add(
        Provento(
            carteira_id=alheia_id,
            ticker="PETR4",
            tipo_provento="Dividendo",
            data_com=date(2024, 5, 1),
            valor_por_acao=Decimal("0.50"),
        )
    )
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

    listagem = await client.get(f"/carteiras/{alheia_id}/proventos")
    assert listagem.status_code == 404, listagem.text
    assert "PETR4" not in listagem.text

    preview = await client.get(
        f"/carteiras/{alheia_id}/proventos/preview",
        params={"ticker": "PETR4", "data_com": "2024-06-01", "valor_por_acao": "0.5"},
    )
    assert preview.status_code == 404, preview.text

    criacao = await client.post(
        f"/carteiras/{alheia_id}/proventos",
        json={
            "ticker": "PETR4",
            "tipo_provento": "Dividendo",
            "data_com": "2024-06-01",
            "valor_por_acao": "0.50",
        },
    )
    assert criacao.status_code == 404, criacao.text
    # O 404 não basta: nada pode ter sido gravado na carteira do outro.
    assert await db_session.scalar(select(func.count()).select_from(Provento)) == 1


async def test_carteira_inexistente_responde_404(
    client, usuario_autenticado, db_session, override_get_db
):
    inexistente = uuid4()
    assert (await client.get(f"/carteiras/{inexistente}/proventos")).status_code == 404
    assert (
        await client.get(f"/carteiras/{inexistente}/proventos/preview")
    ).status_code == 404
