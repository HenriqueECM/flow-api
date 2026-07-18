"""Endpoint de versão: o que o processo sabe sobre si mesmo.

Motivação real, não hipotética: o job `health-check` do CI já documenta que,
durante um deploy no Render Free, `/health` pode responder 200 da instância
ANTIGA (a plataforma mantém a versão anterior no ar enquanto builda a nova) —
e que fechar essa lacuna exigiria "um marcador de versão". É este endpoint.

`commit`/`branch` vêm de `app.core.version`, resolvidos a partir do ambiente
(Render em produção, Actions na CI, "local" fora dos dois).

Sem autenticação, como `/health`: rota operacional, não dado de usuário.
`Cache-Control: no-store` porque um valor cacheado por proxy/CDN logo após um
deploy anularia o propósito da rota — mostraria a versão antiga mesmo com o
processo novo já respondendo.
"""

from datetime import datetime, timezone

from fastapi import APIRouter, Response

from app.core.version import BRANCH, COMMIT, VERSION

router = APIRouter(tags=["version"])

# Calculado uma vez, na carga do módulo (= início do processo) — não a cada
# requisição. Nenhum campo deste endpoint muda durante a vida do processo.
_STARTED_AT = datetime.now(timezone.utc).isoformat()


@router.get("/version")
async def version(response: Response) -> dict[str, str]:
    """Versão e identidade do processo em execução."""
    response.headers["Cache-Control"] = "no-store"
    return {
        "version": VERSION,
        "commit": COMMIT,
        "branch": BRANCH,
        "started_at": _STARTED_AT,
    }
