"""Camada de acesso às cotações da B3 via brapi.dev.

Uma requisição por ticker: o plano da brapi limita a 1 ativo por requisição
(`QUOTES_PER_REQUEST_EXCEEDED`), então as cotações são buscadas individualmente
e agregadas em memória. Um cache (TTL ~5 min por ticker) evita bater na API a
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
# Histórico mensal muda no máximo uma vez por dia (um candle por mês). Cache
# longo evita gastar requisições da brapi em cada abertura do relatório.
_HISTORY_CACHE_TTL_SECONDS = 6 * 60 * 60
_HTTP_TIMEOUT_SECONDS = 10.0

# Cache em memória do processo: ticker -> (expira_em_monotonic, dados).
_quote_cache: dict[str, tuple[float, dict]] = {}
# Cache do histórico mensal: (ticker, range) -> (expira, {"YYYY-MM": close}).
_history_cache: dict[tuple[str, str], tuple[float, dict[str, float]]] = {}


async def _fetch_quotes(tickers: list[str]) -> dict[str, dict]:
    """Busca as cotações na brapi.dev. Retorna dict vazio em caso de falha."""
    if not tickers:
        return {}

    headers: dict[str, str] = {}
    if settings.brapi_token:
        headers["Authorization"] = f"Bearer {settings.brapi_token}"
    else:
        logger.warning("BRAPI_TOKEN não configurado — cotações podem falhar.")

    quotes: dict[str, dict] = {}
    # Um ativo por requisição (limite do plano). Reusa um único cliente HTTP e
    # isola cada ticker: a falha de um não impede os demais.
    async with httpx.AsyncClient(timeout=_HTTP_TIMEOUT_SECONDS) as client:
        for ticker in tickers:
            url = f"{BRAPI_BASE_URL}/quote/{ticker}"
            try:
                response = await client.get(url, headers=headers)
                response.raise_for_status()
                payload = response.json()
            except (httpx.HTTPError, ValueError) as exc:
                # Rede/timeout/JSON inválido: não deixa a aplicação cair nem
                # interrompe os outros tickers.
                logger.warning(
                    "Falha ao consultar brapi.dev: %s: %s", type(exc).__name__, exc
                )
                continue

            for item in payload.get("results") or []:
                symbol = item.get("symbol")
                if not symbol:
                    continue
                quotes[symbol.upper()] = {
                    "regularMarketPrice": item.get("regularMarketPrice"),
                    "regularMarketChangePercent": item.get(
                        "regularMarketChangePercent"
                    ),
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


# ── Histórico mensal (para o relatório de rentabilidade) ──────────────────────

# Faixas válidas da brapi, da menor para a maior. Escolhemos a menor que cobre
# o número de meses pedido — buscar "max" sempre traria payloads grandes à toa.
_HISTORY_RANGES: list[tuple[int, str]] = [
    (12, "1y"),
    (24, "2y"),
    (60, "5y"),
    (120, "10y"),
]


def _range_para_meses(meses: int) -> str:
    for limite, faixa in _HISTORY_RANGES:
        if meses <= limite:
            return faixa
    return "max"


async def _fetch_historico_mensal(ticker: str, faixa: str) -> dict[str, float] | None:
    """Busca o histórico mensal de um ticker na brapi (um candle por mês).

    Retorna `{"YYYY-MM": close}` usando o preço de fechamento REAL do mês (campo
    `close`, não `adjustedClose`): a valoração da carteira multiplica quantidade
    da época pelo preço da época; proventos entram no retorno por fora. Retorna
    `None` em caso de falha (para distinguir de "sem dados").
    """
    headers: dict[str, str] = {}
    if settings.brapi_token:
        headers["Authorization"] = f"Bearer {settings.brapi_token}"

    url = f"{BRAPI_BASE_URL}/quote/{ticker}"
    params = {"range": faixa, "interval": "1mo"}
    try:
        async with httpx.AsyncClient(timeout=_HTTP_TIMEOUT_SECONDS) as client:
            response = await client.get(url, headers=headers, params=params)
            response.raise_for_status()
            payload = response.json()
    except (httpx.HTTPError, ValueError) as exc:
        logger.warning(
            "Falha ao buscar histórico brapi (%s): %s: %s",
            ticker,
            type(exc).__name__,
            exc,
        )
        return None

    results = payload.get("results") or []
    if not results:
        return None

    serie: dict[str, float] = {}
    for candle in results[0].get("historicalDataPrice") or []:
        ts = candle.get("date")
        close = candle.get("close")
        if ts is None or close is None:
            continue
        # `date` é epoch (segundos, UTC). O candle mensal cai no 1º dia do mês;
        # a chave YYYY-MM é o que interessa para casar com o mês da carteira.
        mes = time.strftime("%Y-%m", time.gmtime(ts))
        serie[mes] = float(close)
    return serie


async def get_historico_mensal(
    tickers: list[str], meses: int
) -> dict[str, dict[str, float]]:
    """Histórico mensal de fechamento por ticker: `{ticker: {"YYYY-MM": close}}`.

    `meses` é a profundidade desejada (define a faixa pedida à brapi). Um ativo
    por requisição (limite do plano), com cache longo por (ticker, faixa). Falha
    de um ticker não derruba os demais — ele simplesmente fica ausente do retorno.
    """
    faixa = _range_para_meses(meses)
    now = time.monotonic()

    normalized = list(
        dict.fromkeys(t.strip().upper() for t in tickers if t and t.strip())
    )

    result: dict[str, dict[str, float]] = {}
    for ticker in normalized:
        chave = (ticker, faixa)
        cached = _history_cache.get(chave)
        if cached and cached[0] > now:
            result[ticker] = cached[1]
            continue

        serie = await _fetch_historico_mensal(ticker, faixa)
        if serie is not None:
            _history_cache[chave] = (now + _HISTORY_CACHE_TTL_SECONDS, serie)
            result[ticker] = serie

    return result
