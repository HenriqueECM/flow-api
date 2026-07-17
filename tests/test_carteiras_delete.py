"""Remoção de carteira: posse e cascade.

Único endpoint destrutivo do módulo. A barreira de posse importa mais aqui do
que na leitura: um erro não vaza dados, apaga os de outra pessoa — junto com
todas as transações e proventos dela, por cascade.

Qual cascade este arquivo prova: o do **banco**. O `passive_deletes=True` nos
relationships faz o ORM se abster — ele emite um único `DELETE FROM carteiras` e
o `ON DELETE CASCADE` da FK remove os filhos.

Isso importa porque é o mesmo mecanismo do caminho que não passa por Python:
desde a migration 0004, apagar um usuário no Supabase cascateia por
`auth.users → carteiras → transacoes/proventos` sem nenhum código nosso rodando.
Testar o cascade do ORM provaria o mecanismo errado.

`test_delete_delega_o_cascade_ao_banco` verifica isso diretamente, olhando o SQL
emitido — os demais verificam o efeito.
"""

from datetime import date
from decimal import Decimal
from uuid import UUID, uuid4

import pytest
from sqlalchemy import event, func, select

from app.models import Carteira, Provento, Transacao

NOME_PROPRIO = "Carteira a remover"

# Sem acento: a asserção de vazamento é substring no corpo cru.
NOME_ALHEIO = "Carteira alheia"

OUTRO_USER_ID = UUID("00000000-0000-0000-0000-0000000000bb")


async def _total(db_session, modelo) -> int:
    """Linhas na tabela, lidas do banco — não do identity map da sessão."""
    return await db_session.scalar(select(func.count()).select_from(modelo))


@pytest.fixture
def sql_emitido(engine):
    """Captura os statements que chegam ao Postgres.

    É a única forma de distinguir "o ORM apagou os filhos" de "o banco apagou os
    filhos": os dois produzem o mesmo estado final, e nenhuma asserção sobre
    dados consegue separá-los.
    """
    statements: list[str] = []

    def _capturar(conn, cursor, statement, params, context, executemany):
        statements.append(statement)

    event.listen(engine.sync_engine, "before_cursor_execute", _capturar)
    yield statements
    event.remove(engine.sync_engine, "before_cursor_execute", _capturar)


async def test_usuario_remove_a_propria_carteira(
    client, usuario_autenticado, db_session, override_get_db
):
    carteira = Carteira(user_id=usuario_autenticado.id, nome=NOME_PROPRIO)
    db_session.add(carteira)
    await db_session.commit()
    carteira_id = carteira.id

    assert await _total(db_session, Carteira) == 1

    resposta = await client.delete(f"/carteiras/{carteira_id}")

    assert resposta.status_code == 204, resposta.text
    assert await _total(db_session, Carteira) == 0


async def test_usuario_nao_remove_carteira_de_outro_usuario(
    client, usuario_autenticado, db_session, override_get_db
):
    assert OUTRO_USER_ID != usuario_autenticado.id

    alheia = Carteira(user_id=OUTRO_USER_ID, nome=NOME_ALHEIO)
    db_session.add(alheia)
    await db_session.commit()
    alheia_id = alheia.id

    resposta = await client.delete(f"/carteiras/{alheia_id}")

    assert resposta.status_code == 404, resposta.text
    assert NOME_ALHEIO not in resposta.text

    # A asserção que dá sentido ao teste: um 404 sozinho não distingue "recusou"
    # de "apagou e depois recusou". A linha tem que continuar lá — com o mesmo
    # dono, porque o endpoint escreve e poderia tê-la sequestrado em vez de
    # removido. O `.one()` exige que exista exatamente uma.
    linha = (
        await db_session.execute(
            select(Carteira.id, Carteira.user_id, Carteira.nome).where(
                Carteira.id == alheia_id
            )
        )
    ).one()
    assert linha.user_id == OUTRO_USER_ID
    assert linha.nome == NOME_ALHEIO


async def test_carteira_inexistente_responde_404(
    client, usuario_autenticado, db_session, override_get_db
):
    resposta = await client.delete(f"/carteiras/{uuid4()}")

    assert resposta.status_code == 404, resposta.text


async def test_remover_carteira_remove_transacoes_e_proventos(
    client, usuario_autenticado, db_session, override_get_db
):
    carteira = Carteira(user_id=usuario_autenticado.id, nome=NOME_PROPRIO)
    db_session.add(carteira)
    await db_session.commit()
    carteira_id = carteira.id

    db_session.add_all(
        [
            Transacao(
                carteira_id=carteira_id,
                ticker="PETR4",
                operacao="compra",
                quantidade=Decimal("100"),
                preco_unit=Decimal("30.00"),
                data=date(2024, 1, 10),
            ),
            Transacao(
                carteira_id=carteira_id,
                ticker="PETR4",
                operacao="venda",
                quantidade=Decimal("40"),
                preco_unit=Decimal("35.00"),
                data=date(2024, 3, 5),
            ),
            Provento(
                carteira_id=carteira_id,
                ticker="PETR4",
                tipo_provento="Dividendo",
                data_com=date(2024, 2, 1),
                valor_por_acao=Decimal("0.500000"),
            ),
        ]
    )
    await db_session.commit()

    # Estado antes: sem isto, o "sumiu tudo" do final também seria verdade se os
    # filhos nunca tivessem sido gravados.
    assert await _total(db_session, Transacao) == 2
    assert await _total(db_session, Provento) == 1

    resposta = await client.delete(f"/carteiras/{carteira_id}")

    assert resposta.status_code == 204, resposta.text
    assert await _total(db_session, Carteira) == 0
    assert await _total(db_session, Transacao) == 0
    assert await _total(db_session, Provento) == 0


async def test_delete_delega_o_cascade_ao_banco(
    client, usuario_autenticado, db_session, override_get_db, sql_emitido
):
    """Prova o mecanismo, não o efeito.

    O teste acima ficaria verde tanto se o ORM apagasse os filhos linha a linha
    quanto se o banco os apagasse — o estado final é idêntico. Este separa os
    dois olhando o SQL que chegou ao Postgres.

    Importa porque o cascade do banco é o que roda no caminho sem Python:
    apagar um usuário no Supabase cascateia por auth.users → carteiras → filhos
    (migration 0004). Se o ORM apagasse os filhos aqui, os testes cobririam um
    mecanismo que produção não usa nesse fluxo.
    """
    carteira = Carteira(user_id=usuario_autenticado.id, nome=NOME_PROPRIO)
    db_session.add(carteira)
    await db_session.commit()
    carteira_id = carteira.id

    db_session.add_all(
        [
            Transacao(
                carteira_id=carteira_id,
                ticker="PETR4",
                operacao="compra",
                quantidade=Decimal("100"),
                preco_unit=Decimal("30.00"),
                data=date(2024, 1, 10),
            ),
            Transacao(
                carteira_id=carteira_id,
                ticker="VALE3",
                operacao="compra",
                quantidade=Decimal("50"),
                preco_unit=Decimal("60.00"),
                data=date(2024, 2, 10),
            ),
        ]
    )
    await db_session.commit()
    sql_emitido.clear()  # só interessa o que a requisição emite

    resposta = await client.delete(f"/carteiras/{carteira_id}")

    assert resposta.status_code == 204, resposta.text

    deletes = [s for s in sql_emitido if s.lstrip().upper().startswith("DELETE")]
    # Um único DELETE, e na tabela pai: os filhos somem por conta do banco.
    assert len(deletes) == 1, deletes
    assert "carteiras" in deletes[0]
    assert not any("transacoes" in d for d in deletes), deletes

    # E o ORM não carregou as transações para decidir isso (era o que o
    # passive_deletes evitava): nenhum SELECT em transacoes na requisição.
    selects_filhos = [
        s
        for s in sql_emitido
        if s.lstrip().upper().startswith("SELECT") and "transacoes" in s
    ]
    assert selects_filhos == [], selects_filhos

    # O efeito continua o mesmo — quem o produziu é que mudou.
    assert await _total(db_session, Transacao) == 0
