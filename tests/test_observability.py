"""Logging estruturado: o formatter JSON e a inicialização da observabilidade.

Testes unitários diretos — não sobem o app. O `configure_logging` faz
`dictConfig` global, então o fixture `restaura_logging` devolve o root ao estado
anterior para não contaminar outros testes (ex.: o `caplog` de test_posicoes).
"""

import json
import logging
import sys

import pytest

from app.core.config import settings
from app.core.observability import (
    JsonFormatter,
    _init_sentry,
    configure_logging,
    init_observability,
)


@pytest.fixture
def restaura_logging():
    root = logging.getLogger()
    handlers = root.handlers[:]
    level = root.level
    yield
    root.handlers[:] = handlers
    root.setLevel(level)


def _record(msg="ola %s", args=("mundo",), exc_info=None, level=logging.INFO):
    return logging.LogRecord(
        name="flow.test",
        level=level,
        pathname=__file__,
        lineno=1,
        msg=msg,
        args=args,
        exc_info=exc_info,
    )


def test_formatter_emite_json_valido():
    dados = json.loads(JsonFormatter().format(_record()))

    assert dados["level"] == "INFO"
    assert dados["logger"] == "flow.test"
    assert dados["message"] == "ola mundo"  # % aplicado
    assert "time" in dados
    assert "exc" not in dados  # sem exceção, não polui a linha


def test_formatter_serializa_excecao():
    try:
        raise ValueError("boom")
    except ValueError:
        rec = _record(msg="erro", args=(), exc_info=sys.exc_info(), level=logging.ERROR)

    dados = json.loads(JsonFormatter().format(rec))

    assert dados["level"] == "ERROR"
    assert "boom" in dados["exc"]


def test_configure_logging_instala_handler_json(restaura_logging):
    configure_logging("INFO")

    root = logging.getLogger()
    assert any(isinstance(h.formatter, JsonFormatter) for h in root.handlers)


def test_init_observability_roda_sem_dsn(restaura_logging, monkeypatch):
    # Sem DSN, _init_sentry é no-op; init_observability só configura o logging.
    monkeypatch.setattr(settings, "sentry_dsn", None)

    init_observability()

    root = logging.getLogger()
    assert any(isinstance(h.formatter, JsonFormatter) for h in root.handlers)


def test_init_sentry_noop_sem_dsn(monkeypatch, caplog):
    monkeypatch.setattr(settings, "sentry_dsn", None)

    with caplog.at_level("INFO", logger="flow.observability"):
        _init_sentry()

    assert caplog.text == ""


def test_init_sentry_avisa_com_dsn(monkeypatch, caplog):
    monkeypatch.setattr(settings, "sentry_dsn", "https://exemplo@sentry.io/1")

    with caplog.at_level("INFO", logger="flow.observability"):
        _init_sentry()

    assert "SENTRY_DSN" in caplog.text
