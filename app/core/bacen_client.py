"""Série do CDI (benchmark) via API pública do Banco Central — SGS.

O SGS (Sistema Gerenciador de Séries Temporais) é aberto, gratuito e sem
autenticação. Usamos a série **4391 — Taxa CDI acumulada no mês (% a.m.)**, que
já entrega o retorno percentual de cada mês — exatamente o que o gráfico e a
tabela de rentabilidade comparam. Falhas de rede não derrubam a aplicação:
retornam o que houver em cache (ou vazio), e a linha do CDI some do relatório.
"""

import logging
import time
from datetime import date

import httpx

logger = logging.getLogger("flow.bacen")

# 4391 = CDI acumulado no mês (% a.m.). Um ponto por mês, valor já em pontos
# percentuais (ex.: "1.01" = +1,01% no mês).
BACEN_SGS_URL = "https://api.bcb.gov.br/dados/serie/bcdata.sgs.4391/dados"
_CACHE_TTL_SECONDS = 6 * 60 * 60
_HTTP_TIMEOUT_SECONDS = 10.0

# Cache do processo por data inicial: data_ini_iso -> (expira, {"YYYY-MM": pct}).
_cdi_cache: dict[str, tuple[float, dict[str, float]]] = {}


async def get_cdi_mensal(data_inicio: date) -> dict[str, float]:
    """Retorno mensal do CDI (%) por mês: `{"YYYY-MM": pct}`, a partir de `data_inicio`.

    Ex.: `{"2025-01": 1.01, "2025-02": 0.99}`. Retorna dict vazio em caso de
    falha (a linha do CDI simplesmente não aparece no relatório).
    """
    chave = data_inicio.isoformat()
    now = time.monotonic()

    cached = _cdi_cache.get(chave)
    if cached and cached[0] > now:
        return cached[1]

    params = {
        "formato": "json",
        # O SGS espera a data no formato brasileiro dd/mm/aaaa.
        "dataInicial": data_inicio.strftime("%d/%m/%Y"),
    }
    try:
        async with httpx.AsyncClient(timeout=_HTTP_TIMEOUT_SECONDS) as client:
            response = await client.get(BACEN_SGS_URL, params=params)
            response.raise_for_status()
            payload = response.json()
    except (httpx.HTTPError, ValueError) as exc:
        logger.warning(
            "Falha ao consultar SGS/BACEN (CDI): %s: %s", type(exc).__name__, exc
        )
        return {}

    serie: dict[str, float] = {}
    for item in payload:
        data_str = item.get("data")  # "dd/mm/aaaa"
        valor_str = item.get("valor")
        if not data_str or valor_str is None:
            continue
        try:
            dia, mes, ano = data_str.split("/")
            serie[f"{ano}-{mes}"] = float(valor_str)
        except (ValueError, AttributeError):
            continue

    _cdi_cache[chave] = (now + _CACHE_TTL_SECONDS, serie)
    return serie
