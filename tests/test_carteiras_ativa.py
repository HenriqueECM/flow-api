"""Carteira ativa: a padrão do usuário, garantida pelo banco.

`GET /carteiras/ativa` é o único endpoint de leitura que **escreve**: quando o
usuário ainda não tem carteira, cria a padrão e a devolve. Por misturar as duas
coisas, precisa provar tanto que cria quando falta quanto que *não* cria quando
já existe — um lazy initialization que criasse a cada chamada passaria num teste
que só olhasse o status.

Qual carteira é a "ativa" deixou de ser derivado ("a mais antiga") e passou a ser
explícito: a coluna `is_default`, com um índice único parcial que permite no
máximo uma por usuário. A regra vive no banco, então vale mesmo com várias
instâncias da API — e é o que impede duas chamadas concorrentes de criarem duas
"Minha Carteira".

É também uma das cópias da regra de posse no router: se o filtro por `user_id`
falhar aqui, um usuário adota a carteira de outro como sua.
"""

from datetime import datetime, timezone
from uuid import UUID

import pytest
from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError

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


async def _padroes_de(db_session, user_id: UUID) -> list:
    resultado = await db_session.execute(
        select(Carteira.id).where(
            Carteira.user_id == user_id, Carteira.is_default.is_(True)
        )
    )
    return list(resultado.scalars())


# ── A constraint ─────────────────────────────────────────────────────────────


async def test_banco_recusa_duas_carteiras_padrao_para_o_mesmo_usuario(
    usuario_autenticado, db_session
):
    # O coração da correção: a garantia é do banco, não do código. Duas
    # instâncias da API, ou duas requisições concorrentes, esbarram nisto.
    db_session.add_all(
        [
            Carteira(user_id=usuario_autenticado.id, nome="Uma", is_default=True),
            Carteira(user_id=usuario_autenticado.id, nome="Outra", is_default=True),
        ]
    )

    with pytest.raises(IntegrityError):
        await db_session.commit()

    await db_session.rollback()


async def test_o_limite_e_por_usuario_nao_global(usuario_autenticado, db_session):
    # O índice é parcial sobre user_id: cada usuário tem a sua padrão.
    db_session.add_all(
        [
            Carteira(user_id=usuario_autenticado.id, nome="Minha", is_default=True),
            Carteira(user_id=OUTRO_USER_ID, nome=NOME_ALHEIO, is_default=True),
        ]
    )
    await db_session.commit()

    assert len(await _padroes_de(db_session, usuario_autenticado.id)) == 1
    assert len(await _padroes_de(db_session, OUTRO_USER_ID)) == 1


async def test_varias_carteiras_nao_padrao_sao_permitidas(
    usuario_autenticado, db_session
):
    # O roadmap é múltiplas carteiras (longo prazo, dividendos, trading...). O
    # índice parcial não pode atrapalhar isso — só limita as marcadas.
    db_session.add_all(
        [
            Carteira(user_id=usuario_autenticado.id, nome="Longo prazo"),
            Carteira(user_id=usuario_autenticado.id, nome="Dividendos"),
            Carteira(user_id=usuario_autenticado.id, nome="Trading"),
        ]
    )
    await db_session.commit()

    assert len(await _carteiras_de(db_session, usuario_autenticado.id)) == 3
    assert await _padroes_de(db_session, usuario_autenticado.id) == []


# ── O endpoint ───────────────────────────────────────────────────────────────


async def test_usuario_sem_carteira_recebe_a_padrao_criada_na_hora(
    client, usuario_autenticado, db_session, override_get_db
):
    assert await _carteiras_de(db_session, usuario_autenticado.id) == []

    resposta = await client.get("/carteiras/ativa")

    assert resposta.status_code == 200, resposta.text
    corpo = resposta.json()
    assert corpo["nome"] == NOME_PADRAO
    assert corpo["is_default"] is True

    # A criação tem que ter chegado ao Postgres, e exatamente uma vez.
    ids = await _carteiras_de(db_session, usuario_autenticado.id)
    assert ids == [UUID(corpo["id"])]
    assert await _padroes_de(db_session, usuario_autenticado.id) == ids


async def test_usuario_com_padrao_recebe_a_existente_sem_criar_outra(
    client, usuario_autenticado, db_session, override_get_db
):
    existente = Carteira(
        user_id=usuario_autenticado.id, nome=NOME_EXISTENTE, is_default=True
    )
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
    assert await _carteiras_de(db_session, usuario_autenticado.id) == [existente_id]


async def test_devolve_a_padrao_mesmo_que_nao_seja_a_mais_antiga(
    client, usuario_autenticado, db_session, override_get_db
):
    # A regra mudou de "a mais antiga" para "a marcada". Este teste é o que
    # separa as duas: se o endpoint ainda ordenasse por created_at, devolveria a
    # antiga e reprovaria aqui.
    antiga = Carteira(
        user_id=usuario_autenticado.id,
        nome=NOME_ANTIGA,
        created_at=datetime(2024, 1, 1, tzinfo=timezone.utc),
    )
    nova = Carteira(
        user_id=usuario_autenticado.id,
        nome=NOME_NOVA,
        is_default=True,
        created_at=datetime(2025, 6, 30, tzinfo=timezone.utc),
    )
    db_session.add_all([antiga, nova])
    await db_session.commit()
    nova_id = nova.id

    resposta = await client.get("/carteiras/ativa")

    assert resposta.status_code == 200, resposta.text
    assert resposta.json()["id"] == str(nova_id)
    assert resposta.json()["nome"] == NOME_NOVA


async def test_com_carteiras_sem_padrao_promove_a_mais_antiga(
    client, usuario_autenticado, db_session, override_get_db
):
    # Auto-cura. Acontece com quem criou a primeira carteira via POST /carteiras
    # (que não marca padrão) e com qualquer linha que escape da migração.
    # Promover a mais antiga preserva exatamente o que o endpoint devolvia antes
    # de o is_default existir — ninguém vê o app abrir outra carteira.
    #
    # `created_at` explícito: func.now() é o horário da transação, então linhas
    # do mesmo commit teriam timestamps idênticos e a ordem seria indefinida.
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
    # Inseridas na ordem inversa da esperada: o resultado tem que vir do
    # order_by, não da ordem de inserção.
    db_session.add_all([nova, antiga])
    await db_session.commit()
    antiga_id = antiga.id

    resposta = await client.get("/carteiras/ativa")

    assert resposta.status_code == 200, resposta.text
    corpo = resposta.json()
    assert corpo["id"] == str(antiga_id)
    assert corpo["is_default"] is True

    # Promoveu, não criou: continuam duas carteiras, e só uma é padrão.
    assert len(await _carteiras_de(db_session, usuario_autenticado.id)) == 2
    assert await _padroes_de(db_session, usuario_autenticado.id) == [antiga_id]


async def test_chamadas_repetidas_nao_criam_nem_trocam_a_padrao(
    client, usuario_autenticado, db_session, override_get_db
):
    primeira = await client.get("/carteiras/ativa")
    segunda = await client.get("/carteiras/ativa")
    terceira = await client.get("/carteiras/ativa")

    assert primeira.status_code == 200, primeira.text
    ids = {r.json()["id"] for r in (primeira, segunda, terceira)}
    assert len(ids) == 1
    assert len(await _carteiras_de(db_session, usuario_autenticado.id)) == 1


async def test_carteira_de_outro_usuario_nao_e_adotada_como_ativa(
    client, usuario_autenticado, db_session, override_get_db
):
    assert OUTRO_USER_ID != usuario_autenticado.id

    alheia = Carteira(user_id=OUTRO_USER_ID, nome=NOME_ALHEIO, is_default=True)
    db_session.add(alheia)
    await db_session.commit()
    alheia_id = alheia.id

    # Sem isto o teste seria vazio: o endpoint criaria a padrão do mesmo jeito se
    # o INSERT acima tivesse falhado, e nada de isolamento estaria provado.
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

    assert await _carteiras_de(db_session, usuario_autenticado.id) == [
        UUID(corpo["id"])
    ]
    # A padrão alheia segue intacta e com o dono original.
    assert await _padroes_de(db_session, OUTRO_USER_ID) == [alheia_id]
