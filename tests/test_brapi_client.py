"""Cliente de cotações da brapi.dev.

O cliente engole falhas de rede por design: a cotação é um enriquecimento, e a
API não deve cair porque a brapi caiu. Isso é uma decisão correta e perigosa ao
mesmo tempo — um bug de parsing vira "sem cotação" silencioso —, então os modos
de falha precisam ser exercitados um a um.

Não tocam no banco. As respostas vêm do `respx` já montado por
`bloquear_http_externo`, a mesma fixture que faz qualquer HTTP não declarado
estourar.
"""

import httpx
import pytest

from app.core.brapi_client import get_quotes

URL_PETR4 = "https://brapi.dev/api/quote/PETR4"


def _resultado(symbol="PETR4", preco=30.5, variacao=1.25, nome="Petrobras PN"):
    return {
        "symbol": symbol,
        "regularMarketPrice": preco,
        "regularMarketChangePercent": variacao,
        "shortName": nome,
    }


async def test_resposta_valida_indexa_por_ticker(bloquear_http_externo):
    bloquear_http_externo.get(URL_PETR4).mock(
        return_value=httpx.Response(200, json={"results": [_resultado()]})
    )

    quotes = await get_quotes(["PETR4"])

    assert quotes == {
        "PETR4": {
            "regularMarketPrice": 30.5,
            "regularMarketChangePercent": 1.25,
            "shortName": "Petrobras PN",
        }
    }


async def test_normaliza_e_deduplica_os_tickers(bloquear_http_externo):
    # Uma única chamada em lote, com os tickers em maiúsculas e sem repetição.
    rota = bloquear_http_externo.get(url__regex=r".*/quote/PETR4,VALE3$").mock(
        return_value=httpx.Response(
            200,
            json={"results": [_resultado(), _resultado(symbol="VALE3", nome="Vale")]},
        )
    )

    quotes = await get_quotes([" petr4 ", "PETR4", "vale3", ""])

    assert rota.call_count == 1
    assert set(quotes) == {"PETR4", "VALE3"}


async def test_erro_http_devolve_vazio(bloquear_http_externo):
    bloquear_http_externo.get(URL_PETR4).mock(return_value=httpx.Response(500))

    assert await get_quotes(["PETR4"]) == {}


async def test_timeout_devolve_vazio(bloquear_http_externo):
    bloquear_http_externo.get(URL_PETR4).mock(
        side_effect=httpx.TimeoutException("timeout")
    )

    assert await get_quotes(["PETR4"]) == {}


async def test_json_invalido_devolve_vazio(bloquear_http_externo):
    # A brapi respondendo 200 com corpo que não é JSON (ex.: página de erro de
    # um proxy). O `except` do cliente cobre ValueError justamente para isto.
    bloquear_http_externo.get(URL_PETR4).mock(
        return_value=httpx.Response(200, text="<html>indisponivel</html>")
    )

    assert await get_quotes(["PETR4"]) == {}


async def test_ticker_sem_cotacao_fica_ausente_do_retorno(bloquear_http_externo):
    # Pedir dois e receber um: o ausente não vira chave com valor nulo, some.
    bloquear_http_externo.get(url__regex=r".*/quote/.*").mock(
        return_value=httpx.Response(200, json={"results": [_resultado()]})
    )

    quotes = await get_quotes(["PETR4", "XXXX9"])

    assert set(quotes) == {"PETR4"}


async def test_resultado_sem_symbol_e_ignorado(bloquear_http_externo):
    bloquear_http_externo.get(URL_PETR4).mock(
        return_value=httpx.Response(
            200, json={"results": [{"regularMarketPrice": 10.0}]}
        )
    )

    assert await get_quotes(["PETR4"]) == {}


@pytest.mark.parametrize("payload", [{}, {"results": None}, {"results": []}])
async def test_payload_sem_resultados_devolve_vazio(bloquear_http_externo, payload):
    bloquear_http_externo.get(URL_PETR4).mock(
        return_value=httpx.Response(200, json=payload)
    )

    assert await get_quotes(["PETR4"]) == {}


async def test_cache_evita_uma_segunda_chamada(bloquear_http_externo):
    rota = bloquear_http_externo.get(URL_PETR4).mock(
        return_value=httpx.Response(200, json={"results": [_resultado()]})
    )

    primeira = await get_quotes(["PETR4"])
    segunda = await get_quotes(["PETR4"])

    # A segunda veio da memória: sem isto, cada request do frontend bateria na
    # brapi. (O cache é global do processo; `limpar_cache_brapi` o zera entre
    # os testes.)
    assert rota.call_count == 1
    assert primeira == segunda


async def test_lista_vazia_nao_chama_a_api(bloquear_http_externo):
    # Qualquer requisição aqui estouraria: nenhuma rota foi declarada.
    assert await get_quotes([]) == {}
    assert await get_quotes(["", "  "]) == {}
