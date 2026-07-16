"""Infraestrutura compartilhada dos testes.

Fica na raiz (e não em tests/) para o pytest incluir o diretório do projeto no
sys.path, permitindo `import app.*` nos testes.

As fixtures daqui cobrem a camada HTTP: cliente ASGI, substituição das
dependências do FastAPI e limpeza do estado global entre os testes. Os testes
de motor (services/) não precisam de nada disso e seguem funcionando como antes.
"""

from collections.abc import AsyncGenerator, Callable, Generator
from typing import Any
from uuid import UUID

import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy.engine.url import make_url

# `app.core.config` é seguro de importar aqui: ele só lê o .env/ambiente e não
# constrói o engine. Quem faz isso é `app.core.db`, importado logo abaixo.
from app.core.config import settings

# Únicos hosts aceitos para o banco de testes. Qualquer outro é tratado como
# possível produção e aborta a sessão.
HOSTS_LOCAIS = frozenset({"localhost", "127.0.0.1", "::1"})


def _exigir_banco_de_testes() -> None:
    """Aborta a sessão se DATABASE_URL não apontar para um Postgres local.

    O engine de `app.core.db` nasce no import do módulo, a partir de
    `settings.database_url`, e este conftest importa `app.main` — então toda
    execução de teste tem um engine apontando para onde a URL mandar. Um
    override de `get_db` esquecido bastaria para um teste gravar no Supabase de
    produção, silenciosamente e passando.

    A checagem é feita contra `settings` (e não contra `os.environ`) de
    propósito: é assim que ela enxerga a URL vinda do `.env` de
    desenvolvimento, que é justamente o caso perigoso — no CI a URL já é a do
    container efêmero.
    """
    url = make_url(settings.database_url)
    ajuda = (
        "Exporte uma URL local antes de rodar os testes, por exemplo:\n"
        "  DATABASE_URL=postgresql+asyncpg://flow:flow@localhost:5432/flow_test"
    )

    if url.host not in HOSTS_LOCAIS:
        raise pytest.UsageError(
            f"DATABASE_URL aponta para o host '{url.host}', que não é local. "
            f"Os testes recusam qualquer banco que possa ser produção.\n{ajuda}"
        )

    if url.drivername != "postgresql+asyncpg":
        raise pytest.UsageError(
            f"DATABASE_URL usa o driver '{url.drivername}'; a aplicação exige "
            f"'postgresql+asyncpg'.\n{ajuda}"
        )


# Roda ANTES dos imports abaixo, e é por isso que eles carregam o `noqa: E402`:
# importar `app.main` constrói o engine, e um driver inválido estoura ali com um
# ModuleNotFoundError obscuro (ex.: 'psycopg2') antes de esta guarda ter chance
# de explicar o problema. A ordem aqui é intencional — não reordene.
_exigir_banco_de_testes()

from app.core import brapi_client  # noqa: E402
from app.core.security import CurrentUser, get_current_user  # noqa: E402
from app.main import app  # noqa: E402

# Dono das carteiras nos testes. Fixo (não aleatório) para que uma falha seja
# reproduzível e para permitir comparar o user_id gravado no banco.
TEST_USER_ID = UUID("00000000-0000-0000-0000-000000000001")


@pytest.fixture
def usuario_teste() -> CurrentUser:
    """O usuário autenticado dos testes — o mesmo que `get_current_user`
    devolveria se o token do Supabase fosse válido."""
    return CurrentUser(id=TEST_USER_ID, email="teste@flow.local")


@pytest.fixture
def override_dependency() -> Callable[[Callable[..., Any], Any], None]:
    """Faz uma dependência do FastAPI devolver um valor fixo no teste atual.

    Aceita o valor pronto (`override_dependency(get_db, sessao)`) ou uma função
    que o produza, quando a dependência precisar de argumentos ou levantar erro.
    A limpeza é automática (ver `limpar_dependency_overrides`).
    """

    def _override(dependencia: Callable[..., Any], retorno: Any) -> None:
        app.dependency_overrides[dependencia] = (
            retorno if callable(retorno) else lambda: retorno
        )

    return _override


@pytest.fixture
def usuario_autenticado(
    override_dependency: Callable[..., None], usuario_teste: CurrentUser
) -> CurrentUser:
    """Autentica as requisições do teste, sem token e sem rede.

    `get_current_user` valida o JWT contra o JWKS do Supabase, o que exigiria
    uma chamada HTTP externa e um token assinado de verdade. Substituí-la é o
    que torna os testes de rota determinísticos e offline — a validação real do
    token é assunto de testes unitários do próprio `_decode_token`.
    """
    override_dependency(get_current_user, usuario_teste)
    return usuario_teste


@pytest.fixture
async def client() -> AsyncGenerator[AsyncClient, None]:
    """Cliente HTTP falando direto com o app, em memória (sem servidor/porta).

    Usa `AsyncClient` em vez do `TestClient` síncrono para casar com a stack
    async e permitir que o teste use as mesmas corrotinas do app.

    O `ASGITransport` não dispara o lifespan, então o `create_all` de
    `DEV_CREATE_TABLES` não roda aqui — a criação do schema fica a cargo da
    futura fixture de banco.
    """
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as ac:
        yield ac


@pytest.fixture(autouse=True)
def limpar_dependency_overrides() -> Generator[None, None, None]:
    """`app` é um singleton importado uma vez por sessão: um override deixado
    para trás vazaria para todos os testes seguintes. Limpa depois de cada um."""
    yield
    app.dependency_overrides.clear()


@pytest.fixture(autouse=True)
def limpar_cache_brapi() -> Generator[None, None, None]:
    """Zera o cache de cotações (TTL de 5 min, global do processo).

    Sem isso, um teste que mocka a brapi deixa a cotação em memória e o teste
    seguinte — inclusive o de "brapi fora do ar" — leria do cache e passaria
    por engano. Limpa antes e depois para não depender da ordem de execução.
    """
    brapi_client._quote_cache.clear()
    yield
    brapi_client._quote_cache.clear()
