"""Endpoint /version.

`commit`/`branch` não são fixados aqui: o valor real depende do ambiente onde
o processo roda (produção vs. CI vs. máquina local) — ver
test_version_resolver.py para a lógica de precedência. Este teste trava só o
contrato da rota: campos presentes e o header que impede cache.
"""

from app.core.version import VERSION


async def test_version_responde_com_o_contrato_esperado(client):
    resposta = await client.get("/version")

    assert resposta.status_code == 200, resposta.text
    corpo = resposta.json()
    assert corpo["version"] == VERSION
    assert isinstance(corpo["commit"], str) and corpo["commit"]
    assert isinstance(corpo["branch"], str) and corpo["branch"]
    assert "started_at" in corpo


async def test_version_nao_e_cacheavel(client):
    resposta = await client.get("/version")

    assert resposta.headers["cache-control"] == "no-store"
