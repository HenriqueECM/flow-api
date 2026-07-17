"""Validação das Settings: a DATABASE_URL precisa do driver asyncpg.

O app é async-only (db.py usa create_async_engine). Sem esta guarda, uma URL
`postgresql://` — como a connection string crua do Supabase — passaria pelas
Settings e só estouraria mais tarde, no import de db.py, como um
`ModuleNotFoundError: No module named 'psycopg2'`: erro obscuro, longe da causa.
O validador falha no carregamento das Settings, com a mensagem certa, e NÃO
coage a URL: a configuração correta é obrigatória.
"""

import pytest
from pydantic import ValidationError

from app.core.config import Settings


def _settings(url: str) -> Settings:
    # supabase_jwks_url é obrigatória; valor irrelevante para este teste.
    return Settings(database_url=url, supabase_jwks_url="https://exemplo.invalid/jwks")


def test_aceita_url_com_driver_asyncpg():
    s = _settings("postgresql+asyncpg://flow:flow@localhost:5432/flow_test")
    assert s.database_url.startswith("postgresql+asyncpg://")


@pytest.mark.parametrize(
    "url",
    [
        # Sem driver: cai no dialeto padrão (psycopg2) — o caso que quebrou o CI.
        "postgresql://flow:flow@localhost:5432/flow_test",
        # Esquema curto que o Supabase às vezes mostra.
        "postgres://flow:flow@localhost:5432/flow_test",
        # Driver síncrono explícito: também não serve ao stack async.
        "postgresql+psycopg2://flow:flow@localhost:5432/flow_test",
    ],
)
def test_rejeita_url_sem_driver_asyncpg(url: str):
    with pytest.raises(ValidationError, match="asyncpg"):
        _settings(url)
