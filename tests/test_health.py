"""Primeiro teste de integração: exercita o Postgres de verdade.

Diferente dos testes de motor, este sobe a cadeia inteira do harness —
`engine` → `schema` → `limpar_banco` → `db_session` → `override_get_db` — e por
isso **exige um Postgres de pé**. No CI é o service container; localmente, um
container equivalente e o DATABASE_URL apontando para ele.

`/health` é o alvo certo para o primeiro: é o único endpoint que fala com o
banco sem exigir autenticação, então uma falha aqui é do harness ou da
infraestrutura, nunca de regra de negócio.

O que este teste prova, indiretamente:
- o Postgres subiu e aceita conexão na URL configurada;
- `create_all` rodou — o TRUNCATE do teardown falharia com "relation does not
  exist" se as tabelas não existissem;
- a sessão do teste chega ao endpoint via `Depends(get_db)` e executa SQL real;
- o TRUNCATE roda sem deadlock, ou seja, a `db_session` fechou antes dele.

O que ele NÃO prova: isolamento entre testes. Isso só aparece quando houver dois
testes gravando dados — os de `/carteiras`, na sequência.
"""


async def test_health_responde_ok_com_banco_real(client, override_get_db):
    resposta = await client.get("/health")

    assert resposta.status_code == 200
    assert resposta.json() == {"status": "ok"}
