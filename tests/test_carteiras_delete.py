"""Remoção de carteira: posse e cascade.

Único endpoint destrutivo do módulo. A barreira de posse importa mais aqui do
que na leitura: um erro não vaza dados, apaga os de outra pessoa — junto com
todas as transações e proventos dela, por cascade.

Qual cascade este arquivo prova: o do **ORM**. O cascade está declarado nos dois
lados — `cascade="all, delete-orphan"` nos relationships e `ondelete="CASCADE"`
na FK —, mas o endpoint faz `await db.delete(carteira)`, que carrega as coleções
filhas e emite um DELETE para cada linha. O `ON DELETE CASCADE` do Postgres não
chega a ser exercitado: quando o DELETE da carteira é emitido, os filhos já
foram removidos pelo ORM. Os dois podem divergir sem que nada aqui perceba — um
`DELETE FROM carteiras` em SQL puro dependeria do cascade do banco, que estes
testes não cobrem.
"""

from datetime import date
from decimal import Decimal
from uuid import UUID, uuid4

from sqlalchemy import func, select

from app.models import Carteira, Provento, Transacao

NOME_PROPRIO = "Carteira a remover"

# Sem acento: a asserção de vazamento é substring no corpo cru.
NOME_ALHEIO = "Carteira alheia"

OUTRO_USER_ID = UUID("00000000-0000-0000-0000-0000000000bb")


async def _total(db_session, modelo) -> int:
    """Linhas na tabela, lidas do banco — não do identity map da sessão."""
    return await db_session.scalar(select(func.count()).select_from(modelo))


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
