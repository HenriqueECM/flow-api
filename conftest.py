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
import respx
from httpx import ASGITransport, AsyncClient
from sqlalchemy import text
from sqlalchemy.engine.url import make_url
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession, create_async_engine

# `app.core.config` é seguro de importar aqui: ele só lê o .env/ambiente e não
# constrói o engine. Quem faz isso é `app.core.db`, importado logo abaixo.
from app.core.config import settings

# Únicos hosts aceitos para o banco de testes. Qualquer outro é tratado como
# possível produção e aborta a sessão.
HOSTS_LOCAIS = frozenset({"localhost", "127.0.0.1", "::1"})

# Marca exigida no nome da base. Host local não basta: o Postgres de
# desenvolvimento do próprio dev também é local, e a suíte trunca as tabelas.
MARCA_BANCO_TESTE = "_test"


def _exigir_banco_de_testes() -> None:
    """Aborta a sessão se DATABASE_URL não apontar para um banco de testes.

    O engine de `app.core.db` nasce no import do módulo, a partir de
    `settings.database_url`, e este conftest importa `app.main` — então toda
    execução de teste tem um engine apontando para onde a URL mandar. Um
    override de `get_db` esquecido bastaria para um teste gravar no Supabase de
    produção, silenciosamente e passando.

    São dois riscos distintos, daí duas checagens: o host protege contra bancos
    remotos (produção); o nome da base protege contra o banco de
    desenvolvimento local, que a fixture `limpar_banco` esvaziaria a cada teste.

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

    if not url.database or MARCA_BANCO_TESTE not in url.database:
        raise pytest.UsageError(
            f"DATABASE_URL aponta para a base '{url.database}', cujo nome não "
            f"contém '{MARCA_BANCO_TESTE}'. A suíte esvazia as tabelas a cada "
            f"teste — apontá-la para um banco de desenvolvimento apagaria os "
            f"seus dados.\n{ajuda}"
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
from app.core.db import Base, get_db  # noqa: E402
from app.core.security import CurrentUser, get_current_user  # noqa: E402
from app.main import app  # noqa: E402

# Dono das carteiras nos testes. Fixo (não aleatório) para que uma falha seja
# reproduzível e para permitir comparar o user_id gravado no banco.
TEST_USER_ID = UUID("00000000-0000-0000-0000-000000000001")


# ── Banco de testes ──────────────────────────────────────────────────────────
# Nenhuma destas fixtures é autouse: só quem pedir `engine`/`schema` conecta no
# Postgres. É o que mantém os testes de motor (services/) rodando sem banco
# nenhum, como sempre rodaram.


@pytest.fixture(scope="session")
async def engine() -> AsyncGenerator[AsyncEngine, None]:
    """O engine dos testes — deliberadamente distinto do global de `app.core.db`.

    O global é construído no import a partir da mesma URL, mas é o engine que a
    aplicação usaria em produção; mantê-los separados deixa explícito quem é o
    dono da conexão nos testes e evita depender de estado montado no import.

    Escopo de sessão porque abrir um engine (e criar o schema) por teste seria
    desperdício; exige o loop de sessão configurado no pytest.ini. O `dispose()`
    no teardown fecha o pool — sem ele, a suíte termina com conexões abertas e
    o asyncpg reclama de tarefas pendentes no fim do processo.
    """
    eng = create_async_engine(settings.database_url)
    try:
        yield eng
    finally:
        await eng.dispose()


@pytest.fixture(scope="session")
async def schema(engine: AsyncEngine) -> AsyncGenerator[None, None]:
    """Cria as tabelas a partir dos modelos, uma vez por execução.

    `create_all` e não `sql/schema.sql`: aquele arquivo declara uma FK para
    `auth.users`, schema que só existe dentro do Supabase, e falharia num
    Postgres limpo. A contrapartida é conhecida — o CHECK de `operacao` que o
    schema.sql tem não está nos modelos e, portanto, não existe aqui; o schema
    de teste é um pouco mais permissivo que o de produção. Alembic resolve isso
    depois, tornando os modelos a fonte única.

    Não faz `drop_all` antes: a guarda de import garante que o banco é local,
    mas "local" pode ser o Postgres de desenvolvimento do próprio dev. Como
    `create_all` já é idempotente (checkfirst), o custo de não dropar é apenas
    um schema defasado se os modelos mudarem — resolvido recriando a base à mão.
    """
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    yield


@pytest.fixture
async def limpar_banco(engine: AsyncEngine, schema: None) -> AsyncGenerator[None, None]:
    """Esvazia as tabelas ao fim de cada teste que use o banco.

    Os endpoints commitam de verdade, então sem isso o dado de um teste
    sobreviveria para o seguinte e a suíte passaria a depender da ordem de
    execução.

    As tabelas saem de `Base.metadata` — a mesma fonte do `create_all` —, então
    uma tabela nova é incluída sozinha, sem lista para manter em dia. Um único
    TRUNCATE com todas elas: `CASCADE` cobre as FKs e a ordem deixa de importar.

    Roda no teardown, e só depois que a `db_session` fechou: TRUNCATE exige lock
    ACCESS EXCLUSIVE e ficaria bloqueado por uma transação ainda aberta nas
    mesmas tabelas. É a `db_session` depender desta fixture que garante essa
    ordem — o pytest finaliza na ordem inversa da construção.
    """
    yield
    tabelas = ", ".join(t.name for t in Base.metadata.sorted_tables)
    async with engine.begin() as conn:
        await conn.execute(text(f"TRUNCATE TABLE {tabelas} CASCADE"))


@pytest.fixture
async def db_session(
    engine: AsyncEngine, schema: None, limpar_banco: None
) -> AsyncGenerator[AsyncSession, None]:
    """Uma sessão real do banco, por teste.

    Depende de `schema` e `limpar_banco` pelo efeito colateral: o primeiro
    garante que as tabelas existam antes da sessão abrir; o segundo, que elas
    sejam esvaziadas depois que ela fechar.

    Não reusa o `async_session` de `app.core.db`: aquele sessionmaker está
    amarrado ao engine global. `expire_on_commit=False` espelha a configuração
    da aplicação, para que o comportamento dos objetos após um commit seja o
    mesmo que em produção.

    Escopo de função (a sessão é barata), mas roda no event loop de sessão — o
    mesmo do engine, que é o que faz o pool de conexões funcionar.
    """
    async with AsyncSession(engine, expire_on_commit=False) as session:
        yield session


@pytest.fixture
def override_get_db(
    override_dependency: Callable[..., None], db_session: AsyncSession
) -> AsyncSession:
    """Faz os endpoints receberem a sessão do teste no `Depends(get_db)`.

    Sobrescreve o objeto `get_db` importado de `app.core.db` — o mesmo que os
    routers e `get_owned_carteira` referenciam, então uma única substituição
    cobre todos eles.

    É esta fixture que impede a requisição de cair no engine global montado no
    import. Devolve a mesma sessão para o teste poder arranjar e conferir dados
    exatamente no estado que o endpoint enxerga.

    A limpeza do override é do `limpar_dependency_overrides` (autouse).
    """
    override_dependency(get_db, db_session)
    return db_session


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
def bloquear_http_externo() -> Generator[respx.MockRouter, None, None]:
    """Faz qualquer requisição HTTP real estourar durante os testes.

    Sem isso, um teste de `/posicoes` que esquecesse o mock chamaria a brapi.dev
    de verdade: lento, intermitente e sujeito a rate limit. Pior, o
    `brapi_client` engole falhas de rede por design e devolve `{}` — o teste
    falharia por um motivo que não é o que ele investiga. Aqui a requisição
    esquecida vira um erro explícito, apontando a URL.

    Autouse (ao contrário de `limpar_banco`) porque não custa nada: o respx só
    patcheia o httpx em memória, sem exigir serviço externo nenhum.

    Não registra rota alguma: bloquear é o objetivo, e cada teste declara os
    seus mocks pedindo esta fixture e chamando `router.get(...)`. Por isso
    `assert_all_called=False` — o padrão do respx faria um teste falhar por
    registrar uma rota e não usá-la, o que é asserção de teste, não de harness.

    Não intercepta o `client`: o respx patcheia os pools do httpcore (rede
    real), e o `ASGITransport` fala direto com o app sem passar por lá.
    """
    with respx.mock(assert_all_called=False) as router:
        yield router


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
