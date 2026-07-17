"""Transações: criação, leitura e vínculo com a carteira.

O router só tem GET e POST — não há atualização nem remoção. Transação é fato
histórico: uma compra registrada não se edita, e é dela que todo o resto do
sistema deriva (posições, PM, proventos, relatórios). Um vínculo errado aqui
não corrompe só a listagem, corrompe o cálculo inteiro.

Duas fronteiras distintas são testadas: a de usuário (carteira alheia → 404) e a
de carteira (duas carteiras do MESMO usuário não podem misturar transações).
A segunda não é coberta pelo `get_owned_carteira` — ele valida posse, não
escopo — e é a que produziria um PM errado silenciosamente.

`operacao` é validada em duas camadas, e as duas são testadas: o Literal do
Pydantic recusa na borda (422), e o CHECK do banco recusa o que chegar por
qualquer outro caminho.
"""

from datetime import date
from decimal import Decimal
from uuid import UUID, uuid4

import pytest
from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError

from app.models import Carteira, Transacao

OUTRO_USER_ID = UUID("00000000-0000-0000-0000-0000000000bb")

PAYLOAD = {
    "ticker": "PETR4",
    "nome": "Petrobras PN",
    "tipo_ativo": "Ação",
    "operacao": "compra",
    "quantidade": "150",
    "preco_unit": "32.4567",
    "outros_custos": "4.90",
    "data": "2024-03-15",
    "fonte": "Manual",
}


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


async def test_cria_transacao_e_persiste_com_os_valores_enviados(
    client, usuario_autenticado, db_session, override_get_db
):
    carteira_id = await _carteira(db_session, usuario_autenticado.id)

    resposta = await client.post(f"/carteiras/{carteira_id}/transacoes", json=PAYLOAD)

    assert resposta.status_code == 201, resposta.text
    corpo = resposta.json()
    assert corpo["carteira_id"] == str(carteira_id)

    # Colunas cruas: endpoint e teste dividem a sessão, e um select(Transacao)
    # devolveria o objeto do identity map em vez da linha do Postgres.
    linha = (
        await db_session.execute(
            select(
                Transacao.id,
                Transacao.carteira_id,
                Transacao.ticker,
                Transacao.nome,
                Transacao.tipo_ativo,
                Transacao.operacao,
                Transacao.quantidade,
                Transacao.preco_unit,
                Transacao.outros_custos,
                Transacao.data,
                Transacao.fonte,
            )
        )
    ).one()

    assert str(linha.id) == corpo["id"]
    # O vínculo é o que o resto do sistema usa para achar esta transação.
    assert linha.carteira_id == carteira_id
    assert linha.ticker == "PETR4"
    assert linha.nome == "Petrobras PN"
    assert linha.tipo_ativo == "Ação"
    assert linha.operacao == "compra"
    assert linha.quantidade == Decimal("150")
    # Numeric(20, 4): as 4 casas do preço precisam sobreviver ao round-trip.
    assert linha.preco_unit == Decimal("32.4567")
    assert linha.outros_custos == Decimal("4.90")
    assert linha.data == date(2024, 3, 15)
    assert linha.fonte == "Manual"


async def test_cria_transacao_usa_os_defaults_quando_omitidos(
    client, usuario_autenticado, db_session, override_get_db
):
    carteira_id = await _carteira(db_session, usuario_autenticado.id)
    minimo = {
        "ticker": "VALE3",
        "operacao": "venda",
        "quantidade": "10",
        "preco_unit": "60",
        "data": "2024-05-02",
    }

    resposta = await client.post(f"/carteiras/{carteira_id}/transacoes", json=minimo)

    assert resposta.status_code == 201, resposta.text
    linha = (
        await db_session.execute(
            select(
                Transacao.outros_custos,
                Transacao.fonte,
                Transacao.nome,
                Transacao.tipo_ativo,
            )
        )
    ).one()
    assert linha.outros_custos == Decimal("0")
    assert linha.fonte == "Manual"
    assert linha.nome is None
    assert linha.tipo_ativo is None


async def test_nao_cria_transacao_em_carteira_de_outro_usuario(
    client, usuario_autenticado, db_session, override_get_db
):
    assert OUTRO_USER_ID != usuario_autenticado.id
    alheia_id = await _carteira(db_session, OUTRO_USER_ID, nome="Carteira alheia")

    resposta = await client.post(f"/carteiras/{alheia_id}/transacoes", json=PAYLOAD)

    assert resposta.status_code == 404, resposta.text
    # O 404 não basta: o que importa é que nada foi gravado na carteira do outro.
    assert await db_session.scalar(select(func.count()).select_from(Transacao)) == 0


async def test_lista_traz_as_transacoes_da_carteira_da_mais_recente_para_a_antiga(
    client, usuario_autenticado, db_session, override_get_db
):
    carteira_id = await _carteira(db_session, usuario_autenticado.id)
    db_session.add_all(
        [
            _tx(carteira_id, "PETR4", "compra", 100, "10.00", date(2024, 1, 10)),
            _tx(carteira_id, "VALE3", "compra", 30, "60.00", date(2024, 6, 20)),
            _tx(carteira_id, "PETR4", "venda", 40, "18.00", date(2024, 3, 5)),
        ]
    )
    await db_session.commit()

    resposta = await client.get(f"/carteiras/{carteira_id}/transacoes")

    assert resposta.status_code == 200, resposta.text
    corpo = resposta.json()
    # Ordem é contrato de exibição (extrato: mais recente primeiro). As datas
    # são distintas, então a ordenação é determinística — ao contrário de
    # created_at, que seria idêntico para linhas do mesmo commit.
    assert [t["data"] for t in corpo] == ["2024-06-20", "2024-03-05", "2024-01-10"]
    assert [t["ticker"] for t in corpo] == ["VALE3", "PETR4", "PETR4"]


async def test_lista_nao_mistura_transacoes_de_outra_carteira_do_mesmo_usuario(
    client, usuario_autenticado, db_session, override_get_db
):
    # Ambas do usuário autenticado: aqui o get_owned_carteira aprova as duas, e
    # a única coisa que separa os dados é o filtro por carteira_id no router.
    primeira = await _carteira(db_session, usuario_autenticado.id, nome="Primeira")
    segunda = await _carteira(db_session, usuario_autenticado.id, nome="Segunda")
    db_session.add_all(
        [
            _tx(primeira, "PETR4", "compra", 100, "10.00", date(2024, 1, 10)),
            _tx(segunda, "VALE3", "compra", 30, "60.00", date(2024, 2, 10)),
        ]
    )
    await db_session.commit()

    resposta = await client.get(f"/carteiras/{primeira}/transacoes")

    assert resposta.status_code == 200, resposta.text
    corpo = resposta.json()
    # Igualdade exata: lista vazia reprova, lista com as duas reprova.
    assert [t["ticker"] for t in corpo] == ["PETR4"]
    assert corpo[0]["carteira_id"] == str(primeira)
    assert "VALE3" not in resposta.text


async def test_carteira_de_outro_usuario_nao_expoe_transacoes(
    client, usuario_autenticado, db_session, override_get_db
):
    alheia_id = await _carteira(db_session, OUTRO_USER_ID, nome="Carteira alheia")
    db_session.add(_tx(alheia_id, "PETR4", "compra", 100, "10.00", date(2024, 1, 10)))
    await db_session.commit()

    # Prova que há o que vazar antes de exigir que não vaze.
    assert (
        await db_session.scalar(
            select(func.count())
            .select_from(Transacao)
            .where(Transacao.carteira_id == alheia_id)
        )
        == 1
    )

    resposta = await client.get(f"/carteiras/{alheia_id}/transacoes")

    assert resposta.status_code == 404, resposta.text
    assert "PETR4" not in resposta.text


async def test_carteira_inexistente_responde_404(
    client, usuario_autenticado, db_session, override_get_db
):
    assert (await client.get(f"/carteiras/{uuid4()}/transacoes")).status_code == 404
    assert (
        await client.post(f"/carteiras/{uuid4()}/transacoes", json=PAYLOAD)
    ).status_code == 404


@pytest.mark.parametrize(
    "campo,valor",
    [
        # Field(gt=0): quantidade zero ou negativa não é transação.
        ("quantidade", "0"),
        ("quantidade", "-10"),
        # Field(ge=0): preço negativo não existe; zero é legítimo (bonificação).
        ("preco_unit", "-1"),
        ("outros_custos", "-1"),
        # Literal["compra", "venda"]: o resto do sistema faz `if operacao ==
        # "compra" ... else venda`, então um terceiro valor viraria venda.
        ("operacao", "transferencia"),
        ("ticker", ""),
        ("data", "15/03/2024"),
    ],
)
async def test_payload_invalido_e_recusado_sem_persistir(
    client, usuario_autenticado, db_session, override_get_db, campo, valor
):
    carteira_id = await _carteira(db_session, usuario_autenticado.id)

    resposta = await client.post(
        f"/carteiras/{carteira_id}/transacoes", json={**PAYLOAD, campo: valor}
    )

    assert resposta.status_code == 422, resposta.text
    assert await db_session.scalar(select(func.count()).select_from(Transacao)) == 0


@pytest.mark.parametrize("operacao", ["xpto", "COMPRA", "Compra", "", "transferencia"])
async def test_banco_recusa_operacao_fora_de_compra_venda(
    usuario_autenticado, db_session, operacao
):
    # Sem passar pelo Pydantic — é o caminho que um script de importação, um
    # INSERT manual ou um endpoint futuro tomaria. O motor de posição faz
    # `if operacao == "compra" ... else venda`: qualquer um destes viraria VENDA
    # e corromperia o PM em silêncio. O CHECK é a última linha de defesa.
    #
    # "COMPRA" e "Compra" entram de propósito: a comparação do motor é
    # case-sensitive, então maiúsculas são tão perigosas quanto um valor
    # inventado.
    carteira_id = await _carteira(db_session, usuario_autenticado.id)
    db_session.add(
        Transacao(
            carteira_id=carteira_id,
            ticker="PETR4",
            operacao=operacao,
            quantidade=Decimal("100"),
            preco_unit=Decimal("10"),
            data=date(2024, 1, 10),
        )
    )

    with pytest.raises(IntegrityError):
        await db_session.commit()

    await db_session.rollback()


@pytest.mark.parametrize("operacao", ["compra", "venda"])
async def test_banco_aceita_os_dois_valores_validos(
    usuario_autenticado, db_session, operacao
):
    # O contrapeso do teste acima: o CHECK precisa barrar o inválido sem
    # estorvar o válido.
    carteira_id = await _carteira(db_session, usuario_autenticado.id)
    db_session.add(
        Transacao(
            carteira_id=carteira_id,
            ticker="PETR4",
            operacao=operacao,
            quantidade=Decimal("100"),
            preco_unit=Decimal("10"),
            data=date(2024, 1, 10),
        )
    )
    await db_session.commit()

    gravada = await db_session.scalar(select(Transacao.operacao))
    assert gravada == operacao
