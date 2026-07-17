"""Handler global de erros: exceção não tratada → 500 padronizado e logada.

Monta um app mínimo com o middleware + o handler + uma rota que estoura. O
`raise_app_exceptions=False` no transporte faz o cliente receber a resposta 500
em vez de a exceção "subir" (comportamento padrão do FastAPI em teste).
"""

import logging

import pytest
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient

from app.core.observability import (
    RequestIdFilter,
    RequestIdMiddleware,
    capture_exception,
    unhandled_exception_handler,
)


@pytest.fixture
def app_que_estoura() -> FastAPI:
    app = FastAPI()
    app.add_middleware(RequestIdMiddleware)
    app.add_exception_handler(Exception, unhandled_exception_handler)

    @app.get("/boom")
    async def boom():
        raise ValueError("detalhe interno sensivel")

    return app


async def test_excecao_vira_500_padronizado(app_que_estoura, caplog):
    transport = ASGITransport(app=app_que_estoura, raise_app_exceptions=False)
    async with AsyncClient(transport=transport, base_url="http://test") as ac:
        with caplog.at_level("ERROR", logger="flow.error"):
            resp = await ac.get("/boom", headers={"X-Request-ID": "corr-err"})

    assert resp.status_code == 500
    assert resp.json() == {"detail": "Erro interno."}
    # Não vaza o detalhe interno da exceção no corpo.
    assert "sensivel" not in resp.text

    rec = next(r for r in caplog.records if r.name == "flow.error")
    assert rec.request_id == "corr-err"  # correlacionado via request.state
    assert rec.exc_info is not None  # traceback capturado
    assert "/boom" in caplog.text


def test_filtro_nao_sobrescreve_request_id_existente():
    # O handler de erro põe request_id via extra; o filtro não pode clobbar.
    rec = logging.LogRecord("flow.error", logging.ERROR, __file__, 1, "x", (), None)
    rec.request_id = "ja-tenho"

    RequestIdFilter().filter(rec)

    assert rec.request_id == "ja-tenho"


def test_capture_exception_e_noop_sem_sdk():
    # Stub: não levanta nem faz nada observável enquanto o SDK não existe.
    assert capture_exception(ValueError("x")) is None
