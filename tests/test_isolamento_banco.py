"""Prova que o banco não vaza dados de um teste para o outro.

Estes dois testes não verificam regra de negócio nenhuma: eles verificam o
harness. `limpar_banco` roda um TRUNCATE no teardown de todo teste que usa
`db_session`, e até aqui isso era só intenção — o `/health` exercita o TRUNCATE,
mas sobre tabelas vazias, então não distingue "limpou" de "nunca teve nada".

O par abaixo distingue: o primeiro grava e confirma que gravou; o segundo, que
roda depois, exige encontrar o banco vazio. Se o TRUNCATE não rodar, ou rodar
antes da `db_session` fechar, ou rodar contra as tabelas erradas, é o segundo
teste que acusa.

A ordem importa, e isso é deliberado — é a única forma de observar o estado que
sobrevive (ou não) entre dois testes. O pytest executa na ordem de definição do
arquivo, então o segundo sempre vê o efeito do primeiro. Rodar o segundo
sozinho o faz passar por vacuidade: ele só tem valor depois do primeiro.
"""

from uuid import UUID

from sqlalchemy import func, select

from app.models import Carteira

USER_ID = UUID("00000000-0000-0000-0000-0000000000aa")
NOME = "Carteira do teste de isolamento"


async def test_grava_carteira_e_confirma_que_ela_existe(db_session):
    db_session.add(Carteira(user_id=USER_ID, nome=NOME))
    await db_session.commit()

    # `count` vai ao banco em vez de ler o identity map da sessão, então isto
    # prova que o INSERT chegou ao Postgres — não que o objeto está em memória.
    total = await db_session.scalar(select(func.count()).select_from(Carteira))
    assert total == 1

    nomes = (await db_session.scalars(select(Carteira.nome))).all()
    assert list(nomes) == [NOME]


async def test_teste_seguinte_encontra_o_banco_vazio(db_session):
    # Se o TRUNCATE do teardown anterior não tiver rodado, a carteira gravada
    # pelo teste acima ainda está aqui.
    total = await db_session.scalar(select(func.count()).select_from(Carteira))
    assert total == 0
