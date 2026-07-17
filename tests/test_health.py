"""Liveness e readiness.

A distinção existe por causa do deploy: a plataforma usa o liveness para decidir
se **reinicia** o container. Se ele dependesse do banco, uma oscilação do Supabase
derrubaria a API em laço — reiniciar não conserta um Postgres fora do ar. Os dois
primeiros testes travam essa separação; sem eles, nada impede alguém de "melhorar"
o `/health` acrescentando uma checagem de banco.

`/health/ready` herda o papel que o `/health` tinha: é o único endpoint que fala
com o banco sem exigir autenticação, então é ele que exercita a cadeia inteira do
harness (`engine` → `schema` → `db_session` → `override_get_db`) e prova que o
Postgres subiu, que o `create_all` rodou e que a sessão chega ao endpoint.
"""

from app.core.db import get_db


class _SessaoProibida:
    """Estoura se alguém tocar no banco."""

    async def execute(self, *_args, **_kwargs):
        raise AssertionError("liveness não pode consultar o banco")


class _SessaoForaDoAr:
    """Simula o Postgres inalcançável.

    `ConnectionRefusedError` não é escolha estética: é o que o SQLAlchemy deixa
    escapar quando o banco não responde — um OSError puro, não um
    SQLAlchemyError. Um `except` mais estreito no endpoint erraria exatamente
    este caso.
    """

    async def execute(self, *_args, **_kwargs):
        raise ConnectionRefusedError("banco fora do ar")


async def test_liveness_responde_sem_tocar_no_banco(client, override_dependency):
    # Se alguém acrescentar um Depends(get_db) ao /health, a sessão proibida é
    # injetada, o AssertionError vira 500 e este teste acusa.
    override_dependency(get_db, _SessaoProibida())

    resposta = await client.get("/health")

    assert resposta.status_code == 200, resposta.text
    assert resposta.json() == {"status": "ok"}


async def test_liveness_continua_ok_com_o_banco_fora(client, override_dependency):
    # O ponto da separação: banco fora não pode derrubar o liveness, senão a
    # plataforma recicla a instância por um problema que reiniciar não resolve.
    override_dependency(get_db, _SessaoForaDoAr())

    resposta = await client.get("/health")

    assert resposta.status_code == 200, resposta.text


async def test_readiness_responde_ok_com_banco_real(client, override_get_db):
    resposta = await client.get("/health/ready")

    assert resposta.status_code == 200, resposta.text
    assert resposta.json() == {"status": "ok"}


async def test_readiness_responde_503_com_o_banco_fora(client, override_dependency):
    # 503, e não 500: "não estou pronto" é diferente de "eu quebrei". Um 500 aqui
    # mandaria procurar bug na API quando o problema é o banco.
    override_dependency(get_db, _SessaoForaDoAr())

    resposta = await client.get("/health/ready")

    assert resposta.status_code == 503, resposta.text
    assert resposta.json() == {"detail": "Banco de dados indisponível."}
