"""Endpoint /version.

`commit`/`branch` ainda são placeholders fixos ("local") — a integração com
metadados reais de build é responsabilidade de um commit separado. Este teste
trava o contrato atual da rota: campos, valores-placeholder e o header que
impede cache de servir uma versão desatualizada logo após um deploy.
"""

from app.core.version import VERSION


async def test_version_responde_com_placeholders(client):
    resposta = await client.get("/version")

    assert resposta.status_code == 200, resposta.text
    corpo = resposta.json()
    assert corpo["version"] == VERSION
    assert corpo["commit"] == "local"
    assert corpo["branch"] == "local"
    assert "started_at" in corpo


async def test_version_nao_e_cacheavel(client):
    resposta = await client.get("/version")

    assert resposta.headers["cache-control"] == "no-store"
