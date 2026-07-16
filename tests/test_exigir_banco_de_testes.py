"""Testes da guarda que impede a suíte de rodar contra um banco que não é de
testes.

Ela é a única coisa entre um `pytest` distraído e um banco real: o Supabase de
produção, ou o Postgres de desenvolvimento local, que a fixture `limpar_banco`
esvaziaria a cada teste. Como é lógica de segurança, um afrouxamento acidental
(trocar o host, remover a checagem do nome) precisa quebrar um teste em vez de
passar despercebido numa revisão.

A função lê `settings.database_url`, então cada caso troca esse atributo via
monkeypatch — que o reverte ao fim do teste. Nada aqui toca no Postgres.
"""

import pytest

from app.core.config import settings
from conftest import _exigir_banco_de_testes


def _com_url(monkeypatch: pytest.MonkeyPatch, url: str) -> None:
    monkeypatch.setattr(settings, "database_url", url)


@pytest.mark.parametrize(
    "url",
    [
        # A URL do CI.
        "postgresql+asyncpg://flow:flow@localhost:5432/flow_test",
        # Outros hosts locais aceitos.
        "postgresql+asyncpg://flow:flow@127.0.0.1:5432/flow_test",
        "postgresql+asyncpg://flow:flow@[::1]:5432/flow_test",
        # A marca `_test` vale em qualquer posição do nome, não só no fim.
        "postgresql+asyncpg://flow:flow@localhost:5432/outro_test",
        "postgresql+asyncpg://flow:flow@localhost:5432/flow_test_2",
    ],
)
def test_aceita_banco_de_testes_local(monkeypatch: pytest.MonkeyPatch, url: str):
    _com_url(monkeypatch, url)
    _exigir_banco_de_testes()  # não deve levantar


@pytest.mark.parametrize(
    "url",
    [
        # O caso que motivou a guarda: o Supabase de produção.
        "postgresql+asyncpg://postgres:senha@db.abcxyz.supabase.co:5432/postgres",
        # Nome de base válido não salva um host remoto — as checagens são
        # independentes, e a de host vem primeiro.
        "postgresql+asyncpg://postgres:senha@db.abcxyz.supabase.co:5432/flow_test",
        "postgresql+asyncpg://flow:flow@10.0.0.5:5432/flow_test",
    ],
)
def test_recusa_host_remoto(monkeypatch: pytest.MonkeyPatch, url: str):
    _com_url(monkeypatch, url)
    with pytest.raises(pytest.UsageError, match="não é local"):
        _exigir_banco_de_testes()


@pytest.mark.parametrize(
    "url",
    [
        # O banco de desenvolvimento do dev: local, e seria truncado.
        "postgresql+asyncpg://flow:flow@localhost:5432/flow",
        "postgresql+asyncpg://flow:flow@localhost:5432/postgres",
        # Sem nome de base, a conexão cairia no banco default do usuário.
        "postgresql+asyncpg://flow:flow@localhost:5432",
    ],
)
def test_recusa_base_que_nao_e_de_testes(monkeypatch: pytest.MonkeyPatch, url: str):
    _com_url(monkeypatch, url)
    with pytest.raises(pytest.UsageError, match="não contém"):
        _exigir_banco_de_testes()


@pytest.mark.parametrize(
    "url",
    [
        # Erro comum ao copiar a URL do Supabase, que vem sem o driver async.
        "postgresql://flow:flow@localhost:5432/flow_test",
        "postgresql+psycopg2://flow:flow@localhost:5432/flow_test",
    ],
)
def test_recusa_driver_sincrono(monkeypatch: pytest.MonkeyPatch, url: str):
    _com_url(monkeypatch, url)
    with pytest.raises(pytest.UsageError, match="postgresql\\+asyncpg"):
        _exigir_banco_de_testes()
