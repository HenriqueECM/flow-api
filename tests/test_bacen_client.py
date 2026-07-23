"""Cliente do CDI via SGS/BACEN.

Como o brapi_client, engole falha de rede por design: o CDI é um benchmark
comparativo, e o relatório de rentabilidade não deve cair porque o BACEN caiu.
Os modos de falha são exercitados um a um. Não tocam no banco — as respostas
vêm do `respx` montado por `bloquear_http_externo`.
"""

from datetime import date

import httpx
import pytest

from app.core.bacen_client import get_cdi_mensal

URL_REGEX = r".*bcdata\.sgs\.4391.*"


async def test_parseia_serie_mensal(bloquear_http_externo):
    bloquear_http_externo.get(url__regex=URL_REGEX).mock(
        return_value=httpx.Response(
            200,
            json=[
                {"data": "01/01/2025", "valor": "1.01"},
                {"data": "01/02/2025", "valor": "0.99"},
            ],
        )
    )

    cdi = await get_cdi_mensal(date(2025, 1, 1))

    # A chave YYYY-MM é derivada da data brasileira; o valor já vem em % a.m.
    assert cdi == {"2025-01": 1.01, "2025-02": 0.99}


async def test_erro_http_devolve_vazio(bloquear_http_externo):
    bloquear_http_externo.get(url__regex=URL_REGEX).mock(
        return_value=httpx.Response(500)
    )

    assert await get_cdi_mensal(date(2025, 1, 1)) == {}


async def test_timeout_devolve_vazio(bloquear_http_externo):
    bloquear_http_externo.get(url__regex=URL_REGEX).mock(
        side_effect=httpx.TimeoutException("timeout")
    )

    assert await get_cdi_mensal(date(2025, 1, 1)) == {}


async def test_json_invalido_devolve_vazio(bloquear_http_externo):
    bloquear_http_externo.get(url__regex=URL_REGEX).mock(
        return_value=httpx.Response(200, text="<html>fora do ar</html>")
    )

    assert await get_cdi_mensal(date(2025, 1, 1)) == {}


async def test_itens_malformados_sao_ignorados(bloquear_http_externo):
    # Sem data, sem valor, ou com data que não parseia: pulados sem derrubar o
    # resto da série.
    bloquear_http_externo.get(url__regex=URL_REGEX).mock(
        return_value=httpx.Response(
            200,
            json=[
                {"data": "01/03/2025", "valor": "0.96"},
                {"valor": "1.00"},  # sem data
                {"data": "01/04/2025"},  # sem valor
                {"data": "trimestre", "valor": "1.0"},  # data inválida
            ],
        )
    )

    assert await get_cdi_mensal(date(2025, 1, 1)) == {"2025-03": 0.96}


async def test_cache_evita_segunda_chamada(bloquear_http_externo):
    rota = bloquear_http_externo.get(url__regex=URL_REGEX).mock(
        return_value=httpx.Response(200, json=[{"data": "01/01/2025", "valor": "1.01"}])
    )

    primeira = await get_cdi_mensal(date(2025, 1, 1))
    segunda = await get_cdi_mensal(date(2025, 1, 1))

    assert rota.call_count == 1
    assert primeira == segunda == {"2025-01": 1.01}


@pytest.mark.parametrize("payload", [[], [{"data": "01/01/2025", "valor": "1.0"}]])
async def test_formato_de_data_brasileiro_na_requisicao(bloquear_http_externo, payload):
    # A data inicial vai como dd/mm/aaaa no querystring do SGS.
    rota = bloquear_http_externo.get(url__regex=URL_REGEX).mock(
        return_value=httpx.Response(200, json=payload)
    )

    await get_cdi_mensal(date(2025, 6, 1))

    # O querystring percent-encoda a barra; o valor decodificado é dd/mm/aaaa.
    assert rota.calls[0].request.url.params["dataInicial"] == "01/06/2025"
