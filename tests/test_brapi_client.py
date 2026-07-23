"""Cliente de cotações da brapi.dev.

O cliente engole falhas de rede por design: a cotação é um enriquecimento, e a
API não deve cair porque a brapi caiu. Isso é uma decisão correta e perigosa ao
mesmo tempo — um bug de parsing vira "sem cotação" silencioso —, então os modos
de falha precisam ser exercitados um a um.

Não tocam no banco. As respostas vêm do `respx` já montado por
`bloquear_http_externo`, a mesma fixture que faz qualquer HTTP não declarado
estourar.
"""

import calendar

import httpx
import pytest

from app.core.brapi_client import (
    _range_para_meses,
    get_historico_mensal,
    get_quotes,
)

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
    # Uma requisição por ticker (o plano da brapi limita a 1 ativo/requisição),
    # com os tickers em maiúsculas e sem repetição.
    petr4 = bloquear_http_externo.get(url__regex=r".*/quote/PETR4$").mock(
        return_value=httpx.Response(200, json={"results": [_resultado()]})
    )
    vale3 = bloquear_http_externo.get(url__regex=r".*/quote/VALE3$").mock(
        return_value=httpx.Response(
            200, json={"results": [_resultado(symbol="VALE3", nome="Vale")]}
        )
    )

    quotes = await get_quotes([" petr4 ", "PETR4", "vale3", ""])

    assert petr4.call_count == 1
    assert vale3.call_count == 1
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


# ── Histórico mensal ─────────────────────────────────────────────────────────


def _epoch(ano: int, mes: int) -> int:
    """Epoch (UTC) do 1º dia do mês — como a brapi devolve no candle mensal."""
    return calendar.timegm((ano, mes, 1, 0, 0, 0, 0, 0, 0))


def _candles(pontos: dict[str, float]):
    return {
        "results": [
            {
                "symbol": "PETR4",
                "historicalDataPrice": [
                    {
                        "date": _epoch(*map(int, ym.split("-"))),
                        "close": close,
                        "adjustedClose": close - 1,  # ignorado de propósito
                    }
                    for ym, close in pontos.items()
                ],
            }
        ]
    }


async def test_historico_usa_close_indexado_por_mes(bloquear_http_externo):
    bloquear_http_externo.get(url__regex=r".*/quote/PETR4.*").mock(
        return_value=httpx.Response(
            200, json=_candles({"2025-01": 30.0, "2025-02": 31.5})
        )
    )

    hist = await get_historico_mensal(["PETR4"], 12)

    # Usa `close` (preço da época), não `adjustedClose`.
    assert hist == {"PETR4": {"2025-01": 30.0, "2025-02": 31.5}}


async def test_historico_candle_sem_close_e_ignorado(bloquear_http_externo):
    payload = {
        "results": [
            {
                "symbol": "PETR4",
                "historicalDataPrice": [
                    {"date": _epoch(2025, 1), "close": 30.0},
                    {"date": _epoch(2025, 2)},  # sem close
                    {"close": 31.5},  # sem date
                ],
            }
        ]
    }
    bloquear_http_externo.get(url__regex=r".*/quote/PETR4.*").mock(
        return_value=httpx.Response(200, json=payload)
    )

    assert await get_historico_mensal(["PETR4"], 12) == {"PETR4": {"2025-01": 30.0}}


async def test_historico_erro_http_omite_o_ticker(bloquear_http_externo):
    bloquear_http_externo.get(url__regex=r".*/quote/PETR4.*").mock(
        return_value=httpx.Response(500)
    )

    # Falha não vira {ticker: {}}; o ticker some do retorno.
    assert await get_historico_mensal(["PETR4"], 12) == {}


async def test_historico_sem_resultados_omite_o_ticker(bloquear_http_externo):
    bloquear_http_externo.get(url__regex=r".*/quote/PETR4.*").mock(
        return_value=httpx.Response(200, json={"results": []})
    )

    assert await get_historico_mensal(["PETR4"], 12) == {}


async def test_historico_cache_evita_segunda_chamada(bloquear_http_externo):
    rota = bloquear_http_externo.get(url__regex=r".*/quote/PETR4.*").mock(
        return_value=httpx.Response(200, json=_candles({"2025-01": 30.0}))
    )

    primeira = await get_historico_mensal(["PETR4"], 12)
    segunda = await get_historico_mensal(["PETR4"], 12)

    assert rota.call_count == 1
    assert primeira == segunda


async def test_historico_lista_vazia_nao_chama_a_api(bloquear_http_externo):
    assert await get_historico_mensal([], 12) == {}


def test_selecao_de_faixa_por_profundidade():
    assert _range_para_meses(12) == "1y"
    assert _range_para_meses(24) == "2y"
    assert _range_para_meses(60) == "5y"
    assert _range_para_meses(120) == "10y"
    assert _range_para_meses(121) == "max"
