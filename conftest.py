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

from app.core import brapi_client
from app.core.security import CurrentUser, get_current_user
from app.main import app

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
