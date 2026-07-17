"""Observabilidade: logging estruturado (JSON) e ponto de entrada do Sentry.

Logs em JSON porque é o que o Render e um log drain (Better Stack/Logtail)
ingerem e indexam — texto solto não é pesquisável. O stdout estruturado **é** a
integração com o Better Stack: via log drain, sem código.

`init_observability()` roda no startup (lifespan). O Sentry ainda não é
dependência do projeto; `_init_sentry()` é um stub acionado por `SENTRY_DSN`,
pronto para virar drop-in quando o SDK entrar.
"""

import datetime
import json
import logging
import logging.config

from app.core.config import settings

logger = logging.getLogger("flow.observability")


class JsonFormatter(logging.Formatter):
    """Serializa cada LogRecord como uma linha JSON."""

    def format(self, record: logging.LogRecord) -> str:
        dados = {
            "time": datetime.datetime.fromtimestamp(
                record.created, datetime.timezone.utc
            ).isoformat(),
            "level": record.levelname,
            "logger": record.name,
            "message": record.getMessage(),
        }
        # exc_info só existe em log de exceção; fora disso, não polui a linha.
        if record.exc_info:
            dados["exc"] = self.formatException(record.exc_info)
        return json.dumps(dados, ensure_ascii=False)


def configure_logging(level: str) -> None:
    """Instala o formatter JSON no root e unifica os loggers do uvicorn.

    `disable_existing_loggers=False` para não silenciar os loggers `flow.*` já
    criados no import dos módulos (brapi, posicoes). Os do uvicorn recebem o
    mesmo handler e param de propagar, para não logar em dobro no root.
    """
    logging.config.dictConfig(
        {
            "version": 1,
            "disable_existing_loggers": False,
            "formatters": {"json": {"()": "app.core.observability.JsonFormatter"}},
            "handlers": {
                "stdout": {
                    "class": "logging.StreamHandler",
                    "stream": "ext://sys.stdout",
                    "formatter": "json",
                }
            },
            "root": {"handlers": ["stdout"], "level": level},
            "loggers": {
                nome: {"handlers": ["stdout"], "level": level, "propagate": False}
                for nome in ("uvicorn", "uvicorn.error", "uvicorn.access")
            },
        }
    )


def init_observability() -> None:
    """Configura o logging e inicializa o Sentry (quando houver DSN)."""
    configure_logging(settings.log_level)
    _init_sentry()


def _init_sentry() -> None:
    # Ponto de integração: quando SENTRY_DSN existir, inicializar o SDK aqui
    # (`sentry_sdk.init(dsn=..., ...)`). Stub por ora — o SDK ainda não é
    # dependência do projeto. Sem DSN, no-op silencioso.
    if not settings.sentry_dsn:
        return
    logger.info(
        "SENTRY_DSN presente, mas o SDK do Sentry ainda não está instalado "
        "(stub). Integração pendente."
    )
