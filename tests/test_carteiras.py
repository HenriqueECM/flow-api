"""Testes de domínio de /carteiras.

Primeiro teste que exercita a aplicação de verdade: requisição HTTP real (em
memória, via ASGI), autenticação, validação do payload, persistência no
Postgres e serialização da resposta — tudo no mesmo caminho que o frontend
percorre.
"""

from sqlalchemy import select

from app.models import Carteira

NOME = "Carteira de teste"


async def test_usuario_autenticado_cria_carteira(
    client, usuario_autenticado, db_session, override_get_db
):
    resposta = await client.post("/carteiras", json={"nome": NOME})

    assert resposta.status_code == 201, resposta.text
    corpo = resposta.json()
    assert corpo["nome"] == NOME

    # Lê colunas cruas, não entidades: um `select(Carteira)` devolveria o objeto
    # que o endpoint acabou de criar, já vivo no identity map da sessão, e as
    # asserções passariam mesmo que a linha no banco divergisse. Assim o valor
    # vem da linha do Postgres.
    # O `.one()` também exige que exista exatamente uma carteira.
    linha = (
        await db_session.execute(select(Carteira.id, Carteira.nome, Carteira.user_id))
    ).one()

    assert linha.nome == NOME
    # A resposta não expõe user_id (CarteiraOut não tem o campo), então a posse
    # só pode ser verificada aqui — e é ela que garante que a carteira nasce
    # amarrada a quem chamou, não a um usuário qualquer.
    assert linha.user_id == usuario_autenticado.id
    # Amarra a resposta à linha: o id devolvido é o que foi persistido.
    assert str(linha.id) == corpo["id"]
