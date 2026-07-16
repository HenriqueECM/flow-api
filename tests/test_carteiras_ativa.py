"""Carteira ativa: lazy initialization e isolamento entre usuários.

`GET /carteiras/ativa` é o único endpoint de leitura que **escreve**: quando o
usuário ainda não tem carteira, ele cria a padrão e a devolve. Por misturar as
duas coisas, precisa provar tanto que cria quando falta quanto que *não* cria
quando já existe — um lazy initialization que criasse a cada chamada passaria
num teste que só olhasse o status.

É também a terceira cópia da regra de posse no router (depois de `list` e de
`get_owned_carteira`), e a única num endpoint que grava: se o filtro por
`user_id` falhar aqui, um usuário adota a carteira de outro como sua.
"""

from datetime import datetime, timezone
from uuid import UUID

from sqlalchemy import func, select

from app.models import Carteira

# Precisa casar exatamente com o nome que o endpoint usa ao criar.
NOME_PADRAO = "Minha Carteira"

NOME_ANTIGA = "Carteira antiga"
NOME_NOVA = "Carteira nova"
NOME_EXISTENTE = "Carteira existente"

# Sem acento: a asserção de vazamento é substring no corpo cru.
NOME_ALHEIO = "Carteira alheia"

OUTRO_USER_ID = UUID("00000000-0000-0000-0000-0000000000bb")


async def _carteiras_de(db_session, user_id: UUID) -> list:
    """Ids das carteiras de um usuário, lidos do banco (não do identity map)."""
    resultado = await db_session.execute(
        select(Carteira.id).where(Carteira.user_id == user_id)
    )
    return list(resultado.scalars())


async def test_usuario_sem_carteira_recebe_a_padrao_criada_na_hora(
    client, usuario_autenticado, db_session, override_get_db
):
    assert await _carteiras_de(db_session, usuario_autenticado.id) == []

    resposta = await client.get("/carteiras/ativa")

    assert resposta.status_code == 200, resposta.text
    corpo = resposta.json()
    assert corpo["nome"] == NOME_PADRAO

    # A criação tem que ter chegado ao Postgres, e exatamente uma vez.
    ids = await _carteiras_de(db_session, usuario_autenticado.id)
    assert ids == [UUID(corpo["id"])]


async def test_usuario_com_carteira_recebe_a_existente_sem_criar_outra(
    client, usuario_autenticado, db_session, override_get_db
):
    existente = Carteira(user_id=usuario_autenticado.id, nome=NOME_EXISTENTE)
    db_session.add(existente)
    await db_session.commit()
    existente_id = existente.id

    resposta = await client.get("/carteiras/ativa")

    assert resposta.status_code == 200, resposta.text
    corpo = resposta.json()

    # Devolve a que já existia — não uma nova com o nome padrão.
    assert corpo["id"] == str(existente_id)
    assert corpo["nome"] == NOME_EXISTENTE

    # E o banco continua com uma só: é isto que distingue lazy de "cria sempre".
    ids = await _carteiras_de(db_session, usuario_autenticado.id)
    assert ids == [existente_id]


async def test_com_varias_carteiras_devolve_a_mais_antiga(
    client, usuario_autenticado, db_session, override_get_db
):
    # `created_at` explícito em vez do server_default: `func.now()` devolve o
    # horário da transação, então duas linhas criadas no mesmo commit teriam
    # timestamps idênticos e a ordem seria indefinida — o teste passaria ou
    # falharia por sorte. Fixar as datas torna a regra observável.
    antiga = Carteira(
        user_id=usuario_autenticado.id,
        nome=NOME_ANTIGA,
        created_at=datetime(2024, 1, 1, tzinfo=timezone.utc),
    )
    nova = Carteira(
        user_id=usuario_autenticado.id,
        nome=NOME_NOVA,
        created_at=datetime(2025, 6, 30, tzinfo=timezone.utc),
    )
    # Inseridas na ordem inversa da esperada, para o resultado depender do
    # `order_by(created_at)` do endpoint e não da ordem de inserção.
    db_session.add_all([nova, antiga])
    await db_session.commit()
    antiga_id = antiga.id

    resposta = await client.get("/carteiras/ativa")

    assert resposta.status_code == 200, resposta.text
    corpo = resposta.json()
    assert corpo["id"] == str(antiga_id)
    assert corpo["nome"] == NOME_ANTIGA


async def test_carteira_de_outro_usuario_nao_e_adotada_como_ativa(
    client, usuario_autenticado, db_session, override_get_db
):
    assert OUTRO_USER_ID != usuario_autenticado.id

    alheia = Carteira(user_id=OUTRO_USER_ID, nome=NOME_ALHEIO)
    db_session.add(alheia)
    await db_session.commit()
    alheia_id = alheia.id

    # Sem isto o teste seria vazio: o endpoint criaria a carteira padrão do
    # mesmo jeito se o INSERT acima tivesse falhado, e nada de isolamento
    # estaria sendo provado.
    alheia_existe = await db_session.scalar(
        select(func.count()).select_from(Carteira).where(Carteira.id == alheia_id)
    )
    assert alheia_existe == 1

    resposta = await client.get("/carteiras/ativa")

    assert resposta.status_code == 200, resposta.text
    corpo = resposta.json()

    # Uma carteira NOVA e própria, não a do outro.
    assert corpo["id"] != str(alheia_id)
    assert corpo["nome"] == NOME_PADRAO
    assert NOME_ALHEIO not in resposta.text

    ids = await _carteiras_de(db_session, usuario_autenticado.id)
    assert ids == [UUID(corpo["id"])]

    # A carteira alheia segue intacta e com o dono original.
    assert await _carteiras_de(db_session, OUTRO_USER_ID) == [alheia_id]
