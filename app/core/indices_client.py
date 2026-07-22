"""Histórico de índices de mercado (IBOV) para o benchmark do relatório.

A brapi devolve a cotação SPOT do IBOV (`^BVSP`), mas não o histórico mensal no
plano atual. Como fonte gratuita de histórico usamos o endpoint público de
"chart" do Yahoo Finance (`^BVSP`), que não exige chave — apenas um User-Agent
de navegador. É uma API não-oficial; por isso todo o acesso é tolerante a falha:
se sair do ar, a linha do IBOV apenas some do relatório, sem derrubar nada.

Retornamos o fechamento mensal por mês (`{"YYYY-MM": close}`); o retorno mensal
em % é derivado no motor, a partir de dois fechamentos consecutivos.
"""

import logging
import time

import httpx

logger = logging.getLogger("flow.indices")

# Endpoint público de séries do Yahoo Finance. ^BVSP = Ibovespa.
YAHOO_CHART_URL = "https://query1.finance.yahoo.com/v8/finance/chart/%5EBVSP"
# User-Agent de navegador: sem ele o Yahoo costuma responder 429/403.
_USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/120.0 Safari/537.36"
)
_CACHE_TTL_SECONDS = 6 * 60 * 60
_HTTP_TIMEOUT_SECONDS = 10.0

# Faixas do Yahoo, da menor para a maior (mesma ideia da brapi).
_RANGES: list[tuple[int, str]] = [(12, "1y"), (24, "2y"), (60, "5y"), (120, "10y")]

# Cache do processo por faixa: range -> (expira, {"YYYY-MM": close}).
_ibov_cache: dict[str, tuple[float, dict[str, float]]] = {}


def _range_para_meses(meses: int) -> str:
    for limite, faixa in _RANGES:
        if meses <= limite:
            return faixa
    return "max"


async def get_ibov_mensal(meses: int) -> dict[str, float]:
    """Fechamento mensal do IBOV: `{"YYYY-MM": close}`, com profundidade `meses`.

    Retorna dict vazio em caso de falha (a linha do IBOV some do relatório).
    """
    faixa = _range_para_meses(meses)
    now = time.monotonic()

    cached = _ibov_cache.get(faixa)
    if cached and cached[0] > now:
        return cached[1]

    params = {"range": faixa, "interval": "1mo"}
    try:
        async with httpx.AsyncClient(timeout=_HTTP_TIMEOUT_SECONDS) as client:
            response = await client.get(
                YAHOO_CHART_URL, params=params, headers={"User-Agent": _USER_AGENT}
            )
            response.raise_for_status()
            payload = response.json()
    except (httpx.HTTPError, ValueError) as exc:
        logger.warning(
            "Falha ao consultar Yahoo Finance (IBOV): %s: %s",
            type(exc).__name__,
            exc,
        )
        return {}

    try:
        result = payload["chart"]["result"][0]
        timestamps = result["timestamp"]
        closes = result["indicators"]["quote"][0]["close"]
    except (KeyError, IndexError, TypeError):
        logger.warning("Resposta inesperada do Yahoo Finance (IBOV).")
        return {}

    serie: dict[str, float] = {}
    for ts, close in zip(timestamps, closes):
        if ts is None or close is None:
            continue
        mes = time.strftime("%Y-%m", time.gmtime(ts))
        serie[mes] = float(close)

    _ibov_cache[faixa] = (now + _CACHE_TTL_SECONDS, serie)
    return serie
