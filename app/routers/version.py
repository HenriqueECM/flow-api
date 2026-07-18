"""Endpoint de versão: o que o processo sabe sobre si mesmo.

Motivação real, não hipotética: o job `health-check` do CI já documenta que,
durante um deploy no Render Free, `/health` pode responder 200 da instância
ANTIGA (a plataforma mantém a versão anterior no ar enquanto builda a nova) —
e que fechar essa lacuna exigiria "um marcador de versão". É este endpoint.

`commit`/`branch` ainda são placeholders fixos aqui, de propósito: ligar
RENDER_GIT_COMMIT/GITHUB_SHA (e fallbacks) é responsabilidade de um commit
separado, para este ficar só sobre a existência da rota.

Sem autenticação, como `/health`: rota operacional, não dado de usuário.
`Cache-Control: no-store` porque um valor cacheado por proxy/CDN logo após um
deploy anularia o propósito da rota — mostraria a versão antiga mesmo com o
processo novo já respondendo.
"""

from datetime import datetime, timezone

from fastapi import APIRouter, Response

from app.core.version import VERSION

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
        "commit": "local",
        "branch": "local",
        "started_at": _STARTED_AT,
    }
