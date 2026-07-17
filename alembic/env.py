"""Ambiente do Alembic.

Duas escolhas que valem explicação:

- A URL vem de `settings`, não do `alembic.ini`. O .ini é versionado e a URL de
  produção tem senha; deixá-la lá exigiria um arquivo com segredo no repositório
  (que agora é público). Assim o Alembic usa exatamente a mesma configuração da
  aplicação, e trocar de ambiente é trocar a variável.

- O template é o assíncrono. A aplicação usa asyncpg, e a URL
  (`postgresql+asyncpg://`) só funciona com um engine async — o template síncrono
  tentaria carregar o psycopg2.
"""

import asyncio
from logging.config import fileConfig

from sqlalchemy import pool
from sqlalchemy.engine import Connection
from sqlalchemy.ext.asyncio import async_engine_from_config

from alembic import context
from app.core.config import settings
from app.core.db import Base

# Import com efeito colateral: registra as tabelas em Base.metadata. Sem ele, o
# autogenerate acharia que o banco tem tabelas a mais e geraria drops.
from app.models import Carteira, Provento, Transacao  # noqa: F401

config = context.config

# Injeta a URL da aplicação por cima do que estiver no .ini (que não a define).
#
# O `%%` não é enfeite: o Config do Alembic é um ConfigParser, que interpreta `%`
# como interpolação. Uma senha URL-encoded (`%40` para `@`, comum na connection
# string do Supabase) quebra com "invalid interpolation syntax" antes de qualquer
# conexão. Dobrar o `%` é o escape do ConfigParser, e a leitura via
# `get_main_option` desfaz o escape — a URL chega intacta ao engine.
#
# O CI não pega isto: a URL do container (`flow:flow@localhost`) não tem `%`.
config.set_main_option("sqlalchemy.url", settings.database_url.replace("%", "%%"))

if config.config_file_name is not None:
    fileConfig(config.config_file_name)

target_metadata = Base.metadata

# Objetos que existem no banco de propósito e NÃO estão nos modelos. Sem este
# filtro, o autogenerate os veria como "sobrando" e proporia removê-los.
#
# A FK para auth.users (0004) é o caso: ela não pode ir para models.py porque o
# `create_all` do harness a criaria contra um Postgres sem o schema `auth`, e o
# CI quebraria. Filtrar por nome (e não por heurística de schema) para o filtro
# não engolir, sem querer, uma remoção legítima de outra constraint.
OBJETOS_FORA_DOS_MODELOS = {"fk_carteiras_user_id_auth_users"}


def include_object(objeto, nome, tipo, refletido, comparar_com) -> bool:
    return nome not in OBJETOS_FORA_DOS_MODELOS


def run_migrations_offline() -> None:
    """Gera o SQL sem conectar (`alembic upgrade head --sql`).

    Útil para revisar o que será executado antes de rodar contra o Supabase.
    """
    context.configure(
        url=config.get_main_option("sqlalchemy.url"),
        target_metadata=target_metadata,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
    )

    with context.begin_transaction():
        context.run_migrations()


def do_run_migrations(connection: Connection) -> None:
    context.configure(
        connection=connection,
        target_metadata=target_metadata,
        # Sem isto, o autogenerate ignora mudanças de tipo (ex.: String(20) ->
        # String(40)) e a migration sai incompleta em silêncio.
        compare_type=True,
        compare_server_default=True,
        include_object=include_object,
    )

    with context.begin_transaction():
        context.run_migrations()


async def run_async_migrations() -> None:
    connectable = async_engine_from_config(
        config.get_section(config.config_ini_section, {}),
        prefix="sqlalchemy.",
        poolclass=pool.NullPool,
    )

    async with connectable.connect() as connection:
        await connection.run_sync(do_run_migrations)

    await connectable.dispose()


def run_migrations_online() -> None:
    asyncio.run(run_async_migrations())


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
