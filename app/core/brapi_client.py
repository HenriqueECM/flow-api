"""Camada de acesso às cotações da B3 via brapi.dev.

Uma única chamada busca vários tickers (a brapi aceita lista separada por
vírgula). Um cache em memória (TTL ~5 min por ticker) evita bater na API a
cada request do frontend. Falhas de rede não derrubam a aplicação — retornam
o que houver em cache/vazio.
"""

import logging
import time

import httpx

from app.core.config import settings

logger = logging.getLogger("flow.brapi")

BRAPI_BASE_URL = "https://brapi.dev/api"
_CACHE_TTL_SECONDS = 5 * 60
_HTTP_TIMEOUT_SECONDS = 10.0

# Cache em memória do processo: ticker -> (expira_em_monotonic, dados).
_quote_cache: dict[str, tuple[float, dict]] = {}


async def _fetch_quotes(tickers: list[str]) -> dict[str, dict]:
    """Busca as cotações na brapi.dev. Retorna dict vazio em caso de falha."""
    if not tickers:
        return {}

    url = f"{BRAPI_BASE_URL}/quote/{','.join(tickers)}"
    headers: dict[str, str] = {}
    if settings.brapi_token:
        headers["Authorization"] = f"Bearer {settings.brapi_token}"
    else:
        logger.warning("BRAPI_TOKEN não configurado — cotações podem falhar.")

    try:
        async with httpx.AsyncClient(timeout=_HTTP_TIMEOUT_SECONDS) as client:
            response = await client.get(url, headers=headers)
            response.raise_for_status()
            payload = response.json()
    except (httpx.HTTPError, ValueError) as exc:
        # Rede/timeout/JSON inválido: não deixa a aplicação cair.
        logger.warning("Falha ao consultar brapi.dev: %s: %s", type(exc).__name__, exc)
        return {}

    quotes: dict[str, dict] = {}
    for item in payload.get("results") or []:
        symbol = item.get("symbol")
        if not symbol:
            continue
        quotes[symbol.upper()] = {
            "regularMarketPrice": item.get("regularMarketPrice"),
            "regularMarketChangePercent": item.get("regularMarketChangePercent"),
            "shortName": item.get("shortName"),
        }
    return quotes


async def get_quotes(tickers: list[str]) -> dict[str, dict]:
    """Retorna cotações indexadas por ticker, usando cache de ~5 min.

    Só consulta a brapi.dev os tickers ausentes/expirados no cache — os demais
    vêm direto da memória. Tickers sem cotação (inválidos ou API fora) ficam
    ausentes do dict de retorno.
    """
    now = time.monotonic()

    # Normaliza (upper, sem espaços) e remove duplicados preservando ordem.
    normalized = list(
        dict.fromkeys(t.strip().upper() for t in tickers if t and t.strip())
    )

    result: dict[str, dict] = {}
    missing: list[str] = []
    for ticker in normalized:
        cached = _quote_cache.get(ticker)
        if cached and cached[0] > now:
            result[ticker] = cached[1]
        else:
            missing.append(ticker)

    if missing:
        fetched = await _fetch_quotes(missing)
        expires_at = now + _CACHE_TTL_SECONDS
        for ticker, data in fetched.items():
            _quote_cache[ticker] = (expires_at, data)
            result[ticker] = data

    return result
