"""Observabilidade: logging estruturado (JSON), correlação por request e Sentry.

Logs em JSON porque é o que o Render e um log drain (Better Stack/Logtail)
ingerem e indexam — texto solto não é pesquisável. O stdout estruturado **é** a
integração com o Better Stack: via log drain, sem código.

Correlação: `RequestIdMiddleware` (ASGI puro) põe um `request_id` num ContextVar,
e `RequestIdFilter` o injeta em **todo** LogRecord — inclusive os ad-hoc
(`flow.brapi`, `flow.posicoes`). ASGI puro (e não BaseHTTPMiddleware) porque só
assim o ContextVar propaga de forma confiável para o task do endpoint.

`init_observability()` roda no startup (lifespan). O Sentry ainda não é
dependência do projeto; `_init_sentry()` é um stub acionado por `SENTRY_DSN`.
"""

import datetime
import json
import logging
import logging.config
import time
import uuid
from contextvars import ContextVar

from starlette.requests import Request
from starlette.responses import JSONResponse

from app.core.config import settings

logger = logging.getLogger("flow.observability")
access_logger = logging.getLogger("flow.access")
error_logger = logging.getLogger("flow.error")

# request_id da requisição corrente. Default "-" fora de um request (ex.: logs
# de startup), para o campo existir sempre sem quebrar.
request_id_var: ContextVar[str] = ContextVar("request_id", default="-")

# Atributos padrão de um LogRecord — tudo que NÃO estiver aqui é "extra" (passado
# via `extra=`, como request_id/method/path/status/duration_ms) e vai para o JSON.
_STD_ATTRS = set(logging.LogRecord("", 0, "", 0, "", (), None).__dict__) | {
    "message",
    "asctime",
    "taskName",
}


class RequestIdFilter(logging.Filter):
    """Injeta o request_id do ContextVar em cada LogRecord."""

    def filter(self, record: logging.LogRecord) -> bool:
        # Não sobrescreve um request_id posto via `extra` (ex.: o handler de erro,
        # que roda com o ContextVar já resetado e lê do request.state).
        if not hasattr(record, "request_id"):
            record.request_id = request_id_var.get()
        return True


class JsonFormatter(logging.Formatter):
    """Serializa cada LogRecord como uma linha JSON, com os campos extra."""

    def format(self, record: logging.LogRecord) -> str:
        dados = {
            "time": datetime.datetime.fromtimestamp(
                record.created, datetime.timezone.utc
            ).isoformat(),
            "level": record.levelname,
            "logger": record.name,
            "message": record.getMessage(),
        }
        # Campos extra (request_id, duration_ms, method, path, status, ...).
        for chave, valor in record.__dict__.items():
            if chave not in _STD_ATTRS and not chave.startswith("_"):
                dados[chave] = valor
        # exc_info só existe em log de exceção; fora disso, não polui a linha.
        if record.exc_info:
            dados["exc"] = self.formatException(record.exc_info)
        return json.dumps(dados, ensure_ascii=False, default=str)


def configure_logging(level: str) -> None:
    """Instala o formatter JSON no root, injeta o request_id e ajusta o uvicorn.

    `disable_existing_loggers=False` para não silenciar os loggers `flow.*` já
    criados no import dos módulos. `uvicorn.access` é desligado: quem faz o access
    log é o nosso middleware (JSON, com request_id e duração). `uvicorn.error`
    segue ativo.
    """
    logging.config.dictConfig(
        {
            "version": 1,
            "disable_existing_loggers": False,
            "filters": {"request_id": {"()": "app.core.observability.RequestIdFilter"}},
            "formatters": {"json": {"()": "app.core.observability.JsonFormatter"}},
            "handlers": {
                "stdout": {
                    "class": "logging.StreamHandler",
                    "stream": "ext://sys.stdout",
                    "formatter": "json",
                    "filters": ["request_id"],
                }
            },
            "root": {"handlers": ["stdout"], "level": level},
            "loggers": {
                "uvicorn": {"handlers": ["stdout"], "level": level, "propagate": False},
                "uvicorn.error": {
                    "handlers": ["stdout"],
                    "level": level,
                    "propagate": False,
                },
                # Desligado: substituído pelo nosso access log.
                "uvicorn.access": {
                    "handlers": [],
                    "level": "WARNING",
                    "propagate": False,
                },
            },
        }
    )


class RequestIdMiddleware:
    """Middleware ASGI puro: correlaciona cada request e emite o access log.

    ASGI puro (não BaseHTTPMiddleware) para o ContextVar setado aqui propagar de
    forma confiável ao task do endpoint — assim os logs internos carregam o mesmo
    request_id. Adicionado por último em main.py para ser o mais externo.
    """

    # Health check do Render bate nesses o tempo todo; logá-los inundaria o log.
    # Continuam ganhando o header X-Request-ID, só não geram linha de access.
    SEM_ACCESS_LOG = frozenset({"/health", "/health/ready"})

    def __init__(self, app) -> None:
        self.app = app

    async def __call__(self, scope, receive, send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        cabecalhos = dict(scope.get("headers") or [])
        entrada = cabecalhos.get(b"x-request-id")
        request_id = entrada.decode("latin-1") if entrada else uuid.uuid4().hex
        # Guarda no state para o handler de erro (que roda fora deste middleware,
        # com o ContextVar já resetado) poder ler via request.state.request_id.
        scope.setdefault("state", {})["request_id"] = request_id
        token = request_id_var.set(request_id)

        status = {"code": 500}  # default se a resposta nunca iniciar (exceção crua)

        async def send_wrapper(message) -> None:
            if message["type"] == "http.response.start":
                status["code"] = message["status"]
                message.setdefault("headers", []).append(
                    (b"x-request-id", request_id.encode("latin-1"))
                )
            await send(message)

        inicio = time.perf_counter()
        try:
            await self.app(scope, receive, send_wrapper)
        finally:
            caminho = scope.get("path", "")
            if caminho not in self.SEM_ACCESS_LOG:
                duracao_ms = round((time.perf_counter() - inicio) * 1000, 1)
                metodo = scope.get("method", "-")
                access_logger.info(
                    "%s %s %s",
                    metodo,
                    caminho,
                    status["code"],
                    extra={
                        "method": metodo,
                        "path": caminho,
                        "status": status["code"],
                        "duration_ms": duracao_ms,
                    },
                )
            request_id_var.reset(token)


async def unhandled_exception_handler(request: Request, exc: Exception) -> JSONResponse:
    """Handler global: toda exceção não tratada vira um 500 padronizado.

    Loga em ERROR com traceback, request_id (lido do request.state, já que o
    ContextVar foi resetado pelo middleware) e o endpoint. Não vaza o detalhe
    interno ao cliente. `capture_exception` é o ponto de integração do Sentry.
    """
    request_id = getattr(request.state, "request_id", "-")
    error_logger.error(
        "Erro não tratado em %s %s",
        request.method,
        request.url.path,
        exc_info=exc,
        extra={
            "request_id": request_id,
            "method": request.method,
            "path": request.url.path,
        },
    )
    capture_exception(exc)
    return JSONResponse(status_code=500, content={"detail": "Erro interno."})


def capture_exception(exc: BaseException) -> None:
    # Ponto único de captura para o Sentry: quando o SDK entrar,
    # `sentry_sdk.capture_exception(exc)` vai aqui. No-op por ora.
    return


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
