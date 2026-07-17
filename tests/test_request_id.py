"""Correlação por request: request_id, header X-Request-ID e access log.

Os testes de header/echo/access sobem o app via `client` (ASGITransport passa
pelo middleware). Uso `/health` (liveness, sem banco) e `/carteiras` sem token
(403 antes de tocar o banco) para não depender de Postgres.
"""

import json
import logging

from app.core.observability import (
    JsonFormatter,
    RequestIdFilter,
    RequestIdMiddleware,
    request_id_var,
)


def _record():
    return logging.LogRecord(
        name="flow.test",
        level=logging.INFO,
        pathname=__file__,
        lineno=1,
        msg="oi",
        args=(),
        exc_info=None,
    )


def test_filtro_injeta_request_id():
    token = request_id_var.set("abc123")
    try:
        rec = _record()
        assert RequestIdFilter().filter(rec) is True
        assert rec.request_id == "abc123"
    finally:
        request_id_var.reset(token)


def test_formatter_inclui_request_id_e_extras():
    rec = _record()
    rec.request_id = "abc123"
    rec.duration_ms = 12.3  # extra, como o access log passa

    dados = json.loads(JsonFormatter().format(rec))

    assert dados["request_id"] == "abc123"
    assert dados["duration_ms"] == 12.3


async def test_health_responde_com_header_request_id(client):
    resp = await client.get("/health")

    assert resp.status_code == 200
    assert resp.headers.get("x-request-id")  # gerado, não vazio


async def test_request_id_de_entrada_e_ecoado(client):
    resp = await client.get("/health", headers={"X-Request-ID": "meu-id-123"})

    assert resp.headers.get("x-request-id") == "meu-id-123"


async def test_health_nao_gera_access_log(client, caplog):
    with caplog.at_level("INFO", logger="flow.access"):
        await client.get("/health")

    assert caplog.text == ""  # health é silenciado


async def test_rota_normal_gera_access_log(client, caplog):
    # /carteiras sem token → 403 antes do banco; o access log ainda registra.
    with caplog.at_level("INFO", logger="flow.access"):
        await client.get("/carteiras")

    assert "/carteiras" in caplog.text


async def test_middleware_ignora_nao_http():
    # Escopos não-HTTP (lifespan/websocket) passam direto, sem access log.
    chamado = {}

    async def app_interno(scope, receive, send):
        chamado["ok"] = True

    await RequestIdMiddleware(app_interno)({"type": "lifespan"}, None, None)

    assert chamado["ok"] is True
