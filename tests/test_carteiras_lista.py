"""Listagem de carteiras e isolamento entre usuários.

`GET /carteiras` **não** passa por `get_owned_carteira` — ele tem a sua própria
cópia da regra de posse, filtrando por `user_id` direto no router. Ou seja, a
barreira provada em test_carteiras_leitura.py não protege esta rota, e um erro
aqui vazaria a lista de carteiras de todos os usuários de uma vez.

A resposta (CarteiraOut) não expõe `user_id`, então "pertence ao usuário" só
pode ser verificado cruzando os ids devolvidos com as linhas do banco.
"""

from uuid import UUID

from sqlalchemy import func, select

from app.models import Carteira

NOME_A = "Carteira A"
NOME_B = "Carteira B"
NOME_PROPRIO = "Minha carteira"

# Sem acento de propósito: a asserção de vazamento é uma busca por substring no
# corpo cru, e assim ela não depende de o FastAPI serializar com
# ensure_ascii=False (hoje serializa, mas não é o teste que deve garantir isso).
NOME_ALHEIO = "Carteira alheia"

OUTRO_USER_ID = UUID("00000000-0000-0000-0000-0000000000bb")


async def test_lista_traz_as_carteiras_do_usuario(
    client, usuario_autenticado, db_session, override_get_db
):
    db_session.add_all(
        [
            Carteira(user_id=usuario_autenticado.id, nome=NOME_A),
            Carteira(user_id=usuario_autenticado.id, nome=NOME_B),
        ]
    )
    await db_session.commit()

    resposta = await client.get("/carteiras")

    assert resposta.status_code == 200, resposta.text
    corpo = resposta.json()

    # Conjuntos, não listas: o endpoint ordena por `created_at`, que é
    # `func.now()` — o horário da transação, igual para as duas linhas criadas
    # no mesmo commit. A ordem entre elas é indefinida e não deve ser asserida.
    assert {c["nome"] for c in corpo} == {NOME_A, NOME_B}

    # A posse não aparece na resposta (CarteiraOut não tem user_id), então é
    # verificada cruzando os ids devolvidos com o dono gravado no banco.
    ids = [UUID(c["id"]) for c in corpo]
    donos = (
        await db_session.execute(select(Carteira.user_id).where(Carteira.id.in_(ids)))
    ).scalars()
    assert set(donos) == {usuario_autenticado.id}


async def test_lista_nao_traz_carteira_de_outro_usuario(
    client, usuario_autenticado, db_session, override_get_db
):
    # O cenário só faz sentido se os dois usuários forem mesmo distintos.
    assert OUTRO_USER_ID != usuario_autenticado.id

    minha = Carteira(user_id=usuario_autenticado.id, nome=NOME_PROPRIO)
    alheia = Carteira(user_id=OUTRO_USER_ID, nome=NOME_ALHEIO)
    db_session.add_all([minha, alheia])
    await db_session.commit()
    minha_id, alheia_id = minha.id, alheia.id

    # Sem isto o teste seria vazio: a ausência da carteira alheia na resposta
    # também aconteceria se o INSERT tivesse falhado. Confirmar que ela está no
    # banco é o que transforma a ausência em "a API filtrou algo que existe".
    alheia_existe = await db_session.scalar(
        select(func.count()).select_from(Carteira).where(Carteira.id == alheia_id)
    )
    assert alheia_existe == 1

    resposta = await client.get("/carteiras")

    assert resposta.status_code == 200, resposta.text
    corpo = resposta.json()

    # Igualdade exata, não contagem nem `not in`: exige que a carteira própria
    # esteja presente, então uma API que devolvesse lista vazia — ou que
    # devolvesse tudo — reprova aqui.
    assert [c["id"] for c in corpo] == [str(minha_id)]
    assert corpo[0]["nome"] == NOME_PROPRIO

    # Nem os dados da carteira alheia, nem a existência do id dela.
    assert NOME_ALHEIO not in resposta.text
    assert str(alheia_id) not in resposta.text
