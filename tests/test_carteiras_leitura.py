"""Leitura de carteira e isolamento entre usuários.

`get_owned_carteira` é a única barreira de posse do sistema: todo endpoint
aninhado em /carteiras/{id} passa por ela. Se ela falhar, um usuário lê a
carteira, as transações e os proventos de outro.

A API responde 404 (não 403) para carteira alheia, o que é deliberado: torna
"não existe" e "não é sua" indistinguíveis, de forma que ninguém possa usar a
API como oráculo para descobrir quais ids existem. Os casos 2 e 3 abaixo
verificam justamente que as duas respostas são idênticas.
"""

from uuid import UUID, uuid4

from sqlalchemy import func, select

from app.models import Carteira

NOME_PROPRIO = "Minha carteira"
NOME_ALHEIO = "Carteira de outro usuário"

# Dono da carteira alheia. Só precisa ser um UUID diferente do usuário
# autenticado: `user_id` não tem FK para auth.users nos modelos, então nenhum
# usuário precisa existir de fato para o teste ser válido — o que a barreira
# compara é o UUID.
OUTRO_USER_ID = UUID("00000000-0000-0000-0000-0000000000bb")

# A mensagem de `get_owned_carteira`. Os dois casos de 404 devem devolver
# exatamente isto — nada que diferencie um do outro.
CORPO_404 = {"detail": "Carteira não encontrada."}


async def test_usuario_le_a_propria_carteira(
    client, usuario_autenticado, db_session, override_get_db
):
    carteira = Carteira(user_id=usuario_autenticado.id, nome=NOME_PROPRIO)
    db_session.add(carteira)
    await db_session.commit()
    carteira_id = carteira.id

    resposta = await client.get(f"/carteiras/{carteira_id}")

    assert resposta.status_code == 200, resposta.text
    corpo = resposta.json()
    assert corpo["id"] == str(carteira_id)
    assert corpo["nome"] == NOME_PROPRIO


async def test_usuario_nao_le_carteira_de_outro_usuario(
    client, usuario_autenticado, db_session, override_get_db
):
    # O cenário só faz sentido se os dois usuários forem mesmo distintos.
    assert OUTRO_USER_ID != usuario_autenticado.id

    alheia = Carteira(user_id=OUTRO_USER_ID, nome=NOME_ALHEIO)
    db_session.add(alheia)
    await db_session.commit()
    alheia_id = alheia.id

    resposta = await client.get(f"/carteiras/{alheia_id}")

    assert resposta.status_code == 404, resposta.text

    # Sem esta asserção o teste seria vazio: um 404 também aconteceria se o
    # INSERT acima tivesse falhado, e aí o teste passaria sem provar isolamento
    # nenhum. Confirmar que a linha está no banco é o que transforma o 404 em
    # "a API recusou algo que existe" em vez de "não havia nada para achar".
    ainda_existe = await db_session.scalar(
        select(func.count()).select_from(Carteira).where(Carteira.id == alheia_id)
    )
    assert ainda_existe == 1

    # Nada da carteira alheia pode vazar — nem no corpo, nem numa mensagem de
    # erro que confirme a existência do id.
    assert resposta.json() == CORPO_404
    assert NOME_ALHEIO not in resposta.text
    assert str(OUTRO_USER_ID) not in resposta.text


async def test_carteira_inexistente_responde_404(
    client, usuario_autenticado, db_session, override_get_db
):
    resposta = await client.get(f"/carteiras/{uuid4()}")

    assert resposta.status_code == 404, resposta.text
    # Idêntico ao caso da carteira alheia: a API não revela quais ids existem.
    assert resposta.json() == CORPO_404
