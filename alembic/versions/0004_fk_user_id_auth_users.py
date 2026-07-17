"""fk carteiras.user_id -> auth.users (somente onde o schema auth existe)

Sem esta FK, apagar um usuário no Supabase NÃO apaga as carteiras, transações e
proventos dele: os dados ficam órfãos, inalcançáveis (nenhum user_id os
encontra) e permanentes. Para dados financeiros de pessoa física, isso é
exposição de LGPD, não só sujeira.

A garantia estava prometida no sql/schema.sql, que nunca foi executado — a
introspecção confirmou que a FK nunca existiu (ver 0001).

Por que a migration é condicional: `auth.users` é do Supabase e não existe num
Postgres limpo. O CI cria o schema de teste com `create_all` contra um container
vazio, então uma FK incondicional quebraria o pipeline. Aqui ela é criada só onde
o alvo existe.

A condição roda no banco (bloco DO), não em Python. Em modo offline (`--sql`) não
há conexão para consultar, e checar em Python quebraria o `alembic upgrade head
--sql` — que é o passo de revisão antes de aplicar em produção.

O trade-off, dito com todas as letras: esta migration se comporta de forma
diferente por ambiente, e o CI verde NÃO prova nada sobre ela — no CI ela apenas
não faz nada. A verificação é por introspecção em produção, depois de aplicar.

A FK também NÃO está em app/models.py, pelo mesmo motivo (o create_all a criaria
e falharia). Essa divergência é deliberada e o env.py a protege: sem o filtro
`include_object`, o próximo --autogenerate proporia remover a FK, reabrindo o
problema em silêncio.

Confira antes de aplicar (a migration falha se houver órfãs, e o DDL
transacional reverte tudo):

    select count(*) from carteiras c
    where not exists (select 1 from auth.users u where u.id = c.user_id);

Revision ID: 0004
Revises: 0003
Create Date: 2026-07-17
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "0004"
down_revision: str | Sequence[str] | None = "0003"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

CONSTRAINT = "fk_carteiras_user_id_auth_users"

# A decisão roda no BANCO, não no Python. Assim o mesmo bloco serve para os dois
# modos do Alembic: online (aplica) e offline (`--sql`, que apenas imprime).
#
# Em offline não existe conexão — `op.get_bind()` devolve um MockConnection sem
# `.scalar()`. Checar em Python quebraria o `--sql`, e é justamente ele que
# permite revisar a migration antes de tocar produção.
#
# `to_regclass` devolve NULL em vez de erro quando o objeto não existe, e
# respeita as permissões do papel atual — mais confiável que information_schema,
# que só mostra o que o usuário enxerga.
_CRIAR_FK = f"""
DO $$
BEGIN
    IF to_regclass('auth.users') IS NULL THEN
        RAISE NOTICE 'auth.users não existe: FK {CONSTRAINT} não será criada.';
        RETURN;
    END IF;

    -- NOT VALID + VALIDATE, como na 0003: o ADD direto varreria a tabela
    -- segurando lock que bloqueia escrita. O NOT VALID é instantâneo e o
    -- VALIDATE usa um lock mais brando.
    ALTER TABLE carteiras
        ADD CONSTRAINT {CONSTRAINT}
        FOREIGN KEY (user_id) REFERENCES auth.users (id) ON DELETE CASCADE
        NOT VALID;

    ALTER TABLE carteiras VALIDATE CONSTRAINT {CONSTRAINT};
END
$$;
"""


def upgrade() -> None:
    op.execute(sa.text(_CRIAR_FK))


def downgrade() -> None:
    # IF EXISTS em vez de checar o schema: funciona nos dois ambientes, e o
    # downgrade num banco onde o upgrade não fez nada também não deve fazer.
    op.execute(f"ALTER TABLE carteiras DROP CONSTRAINT IF EXISTS {CONSTRAINT}")
