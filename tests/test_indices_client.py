"""Cliente de índices (IBOV) via Yahoo Finance.

Fonte gratuita e não-oficial usada porque a brapi só expõe a cotação spot do
índice no plano atual. Como as demais fontes, engole falha de rede por design.
Não toca no banco — respostas via `respx` de `bloquear_http_externo`.
"""

import calendar

import httpx

from app.core.indices_client import _range_para_meses, get_ibov_mensal

URL_REGEX = r".*/chart/.*BVSP.*"


def _epoch(ano: int, mes: int) -> int:
    """Epoch (UTC) do 1º dia do mês — como o Yahoo devolve no candle mensal."""
    return calendar.timegm((ano, mes, 1, 0, 0, 0, 0, 0, 0))


def _chart(pontos: dict[str, float]):
    """Monta um payload no formato do endpoint /chart do Yahoo."""
    timestamps = []
    closes = []
    for ym, close in pontos.items():
        ano, mes = map(int, ym.split("-"))
        timestamps.append(_epoch(ano, mes))
        closes.append(close)
    return {
        "chart": {
            "result": [
                {
                    "timestamp": timestamps,
                    "indicators": {"quote": [{"close": closes}]},
                }
            ]
        }
    }


async def test_parseia_fechamento_mensal(bloquear_http_externo):
    bloquear_http_externo.get(url__regex=URL_REGEX).mock(
        return_value=httpx.Response(
            200, json=_chart({"2025-01": 120000.0, "2025-02": 122000.0})
        )
    )

    ibov = await get_ibov_mensal(12)

    assert ibov == {"2025-01": 120000.0, "2025-02": 122000.0}


async def test_pontos_nulos_sao_ignorados(bloquear_http_externo):
    # O Yahoo às vezes devolve close=None em meses sem pregão consolidado.
    payload = {
        "chart": {
            "result": [
                {
                    "timestamp": [_epoch(2025, 1), _epoch(2025, 2)],
                    "indicators": {"quote": [{"close": [120000.0, None]}]},
                }
            ]
        }
    }
    bloquear_http_externo.get(url__regex=URL_REGEX).mock(
        return_value=httpx.Response(200, json=payload)
    )

    assert await get_ibov_mensal(12) == {"2025-01": 120000.0}


async def test_erro_http_devolve_vazio(bloquear_http_externo):
    bloquear_http_externo.get(url__regex=URL_REGEX).mock(
        return_value=httpx.Response(503)
    )

    assert await get_ibov_mensal(12) == {}


async def test_timeout_devolve_vazio(bloquear_http_externo):
    bloquear_http_externo.get(url__regex=URL_REGEX).mock(
        side_effect=httpx.TimeoutException("timeout")
    )

    assert await get_ibov_mensal(12) == {}


async def test_estrutura_inesperada_devolve_vazio(bloquear_http_externo):
    # Yahoo respondendo 200 mas com "chart.result" nulo (ex.: símbolo inválido).
    bloquear_http_externo.get(url__regex=URL_REGEX).mock(
        return_value=httpx.Response(200, json={"chart": {"result": None}})
    )

    assert await get_ibov_mensal(12) == {}


async def test_cache_evita_segunda_chamada(bloquear_http_externo):
    rota = bloquear_http_externo.get(url__regex=URL_REGEX).mock(
        return_value=httpx.Response(200, json=_chart({"2025-01": 120000.0}))
    )

    primeira = await get_ibov_mensal(12)
    segunda = await get_ibov_mensal(12)

    assert rota.call_count == 1
    assert primeira == segunda == {"2025-01": 120000.0}


def test_selecao_de_faixa_por_profundidade():
    assert _range_para_meses(12) == "1y"
    assert _range_para_meses(13) == "2y"
    assert _range_para_meses(60) == "5y"
    assert _range_para_meses(120) == "10y"
    assert _range_para_meses(200) == "max"
