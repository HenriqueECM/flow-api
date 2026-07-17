"""Confirmação do import: persistência, duplicatas e validação de posição.

Substitui a versão que chamava a corrotina do endpoint com um `_FakeSession`.
Aquele contorno existia porque, quando foi escrito, o projeto não tinha banco de
testes — e ele provava pouco: uma sessão falsa aceita `add()` sem gravar nada,
então "criadas == 1" não dizia que a linha chegou ao Postgres, e `detectar_
duplicatas_no_banco` comparava contra objetos montados à mão, nunca contra o que
o banco de fato devolve (onde Numeric(20,8) volta como Decimal('100.00000000')).

Agora o fluxo é o real: requisição HTTP → dependência de posse → sessão →
Postgres.
"""

from datetime import date
from decimal import Decimal
from uuid import UUID, uuid4

from sqlalchemy import func, select

from app.models import Carteira, Transacao

OUTRO_USER_ID = UUID("00000000-0000-0000-0000-0000000000bb")

MOTIVO_DUPLICATA = (
    "Possível duplicata — já existe uma transação idêntica "
    "(mesmo ticker, data, quantidade e preço) nesta carteira."
)


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
        outros_custos=Decimal(0),
        data=dia,
    )


def _row(ativo, tipo, qtd, preco, data_iso):
    """Uma ReviewRow como o frontend a envia — chaves em camelCase."""
    return {
        "status": "valido",
        "ativo": ativo,
        "qtde": qtd,
        "tipo": tipo,
        "precoMedio": preco,
        "data": data_iso,
    }


async def _transacoes(db_session):
    """Colunas cruas das transações, em ordem de inserção."""
    return (
        await db_session.execute(
            select(
                Transacao.ticker,
                Transacao.operacao,
                Transacao.quantidade,
                Transacao.preco_unit,
                Transacao.outros_custos,
                Transacao.data,
                Transacao.fonte,
                Transacao.carteira_id,
            ).order_by(Transacao.created_at, Transacao.ticker)
        )
    ).all()


async def test_confirm_persiste_as_linhas_no_postgres(
    client, usuario_autenticado, db_session, override_get_db
):
    carteira_id = await _carteira(db_session, usuario_autenticado.id)

    resposta = await client.post(
        f"/carteiras/{carteira_id}/import/ativos/confirm",
        json={"rows": [_row("PETR4", "Compra", 100.0, 35.0, "2024-01-05")]},
    )

    assert resposta.status_code == 200, resposta.text
    assert resposta.json() == {"criadas": 1, "falhas": []}

    # O que a sessão falsa não conseguia provar: a linha existe no banco, com os
    # valores certos e amarrada à carteira certa.
    linhas = await _transacoes(db_session)
    assert len(linhas) == 1
    tx = linhas[0]
    assert tx.ticker == "PETR4"
    assert tx.operacao == "compra"
    assert tx.quantidade == Decimal("100")
    assert tx.preco_unit == Decimal("35")
    assert tx.outros_custos == Decimal("0")
    assert tx.data == date(2024, 1, 5)
    # A origem distingue o que veio do import do que foi digitado à mão.
    assert tx.fonte == "Importação B3"
    assert tx.carteira_id == carteira_id


async def test_confirm_pula_duplicata_do_banco_e_persiste_a_nova(
    client, usuario_autenticado, db_session, override_get_db
):
    carteira_id = await _carteira(db_session, usuario_autenticado.id)
    db_session.add(_tx(carteira_id, "PETR4", "compra", 100, 30, date(2024, 1, 5)))
    await db_session.commit()

    resposta = await client.post(
        f"/carteiras/{carteira_id}/import/ativos/confirm",
        json={
            "rows": [
                # Idêntica à que já está no banco.
                _row("PETR4", "Compra", 100.0, 30.0, "2024-01-05"),
                _row("VALE3", "Compra", 50.0, 60.0, "2024-02-01"),
            ]
        },
    )

    assert resposta.status_code == 200, resposta.text
    corpo = resposta.json()
    assert corpo["criadas"] == 1
    assert corpo["falhas"] == [{"ativo": "PETR4", "motivo": MOTIVO_DUPLICATA}]

    # Reimportar o mesmo arquivo não pode duplicar a posição: continuam duas
    # transações — a original e a nova.
    tickers = [t.ticker for t in await _transacoes(db_session)]
    assert sorted(tickers) == ["PETR4", "VALE3"]


async def test_confirm_compara_duplicata_contra_o_decimal_que_o_banco_devolve(
    client, usuario_autenticado, db_session, override_get_db
):
    # A transação volta do Postgres como Decimal('100.00000000') (Numeric(20,8))
    # e Decimal('30.0000'); a linha do lote chega como float 100.0/30.0. A
    # detecção só funciona porque Decimals iguais em valor hasham igual — algo
    # que a sessão falsa nunca exercitou, já que devolvia os objetos montados no
    # próprio teste, sem passar pelo banco.
    carteira_id = await _carteira(db_session, usuario_autenticado.id)
    db_session.add(
        _tx(carteira_id, "PETR4", "compra", "100.00000000", "30.0000", date(2024, 1, 5))
    )
    await db_session.commit()
    db_session.expunge_all()  # força a releitura da linha, sem identity map

    resposta = await client.post(
        f"/carteiras/{carteira_id}/import/ativos/confirm",
        json={"rows": [_row("PETR4", "Compra", 100.0, 30.0, "2024-01-05")]},
    )

    assert resposta.status_code == 200, resposta.text
    corpo = resposta.json()
    assert corpo["criadas"] == 0
    assert corpo["falhas"] == [{"ativo": "PETR4", "motivo": MOTIVO_DUPLICATA}]
    assert await db_session.scalar(select(func.count()).select_from(Transacao)) == 1


async def test_duplicata_exige_correspondencia_exata(
    client, usuario_autenticado, db_session, override_get_db
):
    carteira_id = await _carteira(db_session, usuario_autenticado.id)
    db_session.add(_tx(carteira_id, "PETR4", "compra", 100, 30, date(2024, 1, 5)))
    await db_session.commit()

    resposta = await client.post(
        f"/carteiras/{carteira_id}/import/ativos/confirm",
        json={
            "rows": [
                # Mesma data/qtd/preço, operação diferente: não é duplicata.
                _row("PETR4", "Venda", 100.0, 30.0, "2024-01-05"),
                # Mesma data/qtd/operação, preço diferente: não é duplicata.
                _row("PETR4", "Compra", 100.0, 31.0, "2024-01-05"),
            ]
        },
    )

    assert resposta.status_code == 200, resposta.text
    assert resposta.json() == {"criadas": 2, "falhas": []}
    assert await db_session.scalar(select(func.count()).select_from(Transacao)) == 3


async def test_linhas_iguais_do_mesmo_lote_nao_sao_duplicata_entre_si(
    client, usuario_autenticado, db_session, override_get_db
):
    # Duas compras idênticas no mesmo dia são raras, mas legítimas — a checagem
    # é só contra o banco, nunca dentro do lote.
    carteira_id = await _carteira(db_session, usuario_autenticado.id)

    resposta = await client.post(
        f"/carteiras/{carteira_id}/import/ativos/confirm",
        json={
            "rows": [
                _row("ITUB4", "Compra", 100.0, 32.0, "2024-03-01"),
                _row("ITUB4", "Compra", 100.0, 32.0, "2024-03-01"),
            ]
        },
    )

    assert resposta.status_code == 200, resposta.text
    assert resposta.json() == {"criadas": 2, "falhas": []}
    assert await db_session.scalar(select(func.count()).select_from(Transacao)) == 2


async def test_venda_que_excede_a_posicao_falha_sem_bloquear_as_demais(
    client, usuario_autenticado, db_session, override_get_db
):
    carteira_id = await _carteira(db_session, usuario_autenticado.id)

    resposta = await client.post(
        f"/carteiras/{carteira_id}/import/ativos/confirm",
        json={
            "rows": [
                _row("PETR4", "Compra", 100.0, 30.0, "2024-01-05"),
                # Vende mais do que tem: só esta linha falha.
                _row("PETR4", "Venda", 500.0, 35.0, "2024-02-05"),
                _row("VALE3", "Compra", 50.0, 60.0, "2024-02-10"),
            ]
        },
    )

    assert resposta.status_code == 200, resposta.text
    corpo = resposta.json()
    assert corpo["criadas"] == 2
    assert len(corpo["falhas"]) == 1
    assert corpo["falhas"][0]["ativo"] == "PETR4"

    # A falha de uma linha não pode descartar o lote inteiro.
    tickers = sorted(t.ticker for t in await _transacoes(db_session))
    assert tickers == ["PETR4", "VALE3"]


async def test_posicao_do_lote_considera_o_que_ja_esta_no_banco(
    client, usuario_autenticado, db_session, override_get_db
):
    # 100 no banco + venda de 150 no lote: excede, mesmo o lote não tendo compra.
    carteira_id = await _carteira(db_session, usuario_autenticado.id)
    db_session.add(_tx(carteira_id, "PETR4", "compra", 100, 30, date(2024, 1, 5)))
    await db_session.commit()

    resposta = await client.post(
        f"/carteiras/{carteira_id}/import/ativos/confirm",
        json={"rows": [_row("PETR4", "Venda", 150.0, 35.0, "2024-02-05")]},
    )

    assert resposta.status_code == 200, resposta.text
    assert resposta.json()["criadas"] == 0
    # Nada foi gravado: continua só a compra original.
    assert await db_session.scalar(select(func.count()).select_from(Transacao)) == 1


async def test_linha_sem_data_falha_sem_derrubar_o_lote(
    client, usuario_autenticado, db_session, override_get_db
):
    carteira_id = await _carteira(db_session, usuario_autenticado.id)
    sem_data = _row("PETR4", "Compra", 100.0, 30.0, None)

    resposta = await client.post(
        f"/carteiras/{carteira_id}/import/ativos/confirm",
        json={"rows": [sem_data, _row("VALE3", "Compra", 50.0, 60.0, "2024-02-01")]},
    )

    # Erro controlado por linha, não 500 nem 422 do lote inteiro.
    assert resposta.status_code == 200, resposta.text
    corpo = resposta.json()
    assert corpo["criadas"] == 1
    assert corpo["falhas"][0]["ativo"] == "PETR4"

    tickers = [t.ticker for t in await _transacoes(db_session)]
    assert tickers == ["VALE3"]


async def test_lote_vazio_nao_grava_nem_falha(
    client, usuario_autenticado, db_session, override_get_db
):
    carteira_id = await _carteira(db_session, usuario_autenticado.id)

    resposta = await client.post(
        f"/carteiras/{carteira_id}/import/ativos/confirm", json={"rows": []}
    )

    assert resposta.status_code == 200, resposta.text
    assert resposta.json() == {"criadas": 0, "falhas": []}
    assert await db_session.scalar(select(func.count()).select_from(Transacao)) == 0


async def test_confirm_em_carteira_alheia_nao_grava_nada(
    client, usuario_autenticado, db_session, override_get_db
):
    assert OUTRO_USER_ID != usuario_autenticado.id
    alheia_id = await _carteira(db_session, OUTRO_USER_ID, nome="Carteira alheia")

    # Prova que a carteira existe: sem isto o 404 seria indistinguível de
    # "carteira não existe" e nada de isolamento estaria provado.
    assert (
        await db_session.scalar(
            select(func.count()).select_from(Carteira).where(Carteira.id == alheia_id)
        )
        == 1
    )

    resposta = await client.post(
        f"/carteiras/{alheia_id}/import/ativos/confirm",
        json={"rows": [_row("PETR4", "Compra", 100.0, 35.0, "2024-01-05")]},
    )

    assert resposta.status_code == 404, resposta.text
    assert await db_session.scalar(select(func.count()).select_from(Transacao)) == 0


async def test_confirm_em_carteira_inexistente_responde_404(
    client, usuario_autenticado, db_session, override_get_db
):
    resposta = await client.post(
        f"/carteiras/{uuid4()}/import/ativos/confirm",
        json={"rows": [_row("PETR4", "Compra", 100.0, 35.0, "2024-01-05")]},
    )

    assert resposta.status_code == 404, resposta.text
