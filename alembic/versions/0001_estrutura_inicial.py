"""estrutura inicial

Baseline: descreve o schema como ele já existe hoje. Nada muda de estrutura.

Escrita à mão, não por autogenerate — o autogenerate compara os modelos com um
banco vivo, e a estrutura aqui foi derivada do DDL que o próprio SQLAlchemy emite
para `Base.metadata`. O resultado é o mesmo que o `create_all` produz, que é o
que os testes já usam.

Confere com produção. A introspecção do banco real (índices `ix_*`, ausência de
FK para auth.users, ausência de CHECK em `operacao`, defaults só em `id` e
`created_at`) mostrou que as tabelas nasceram do `create_all`, e não do
`sql/schema.sql` — que nunca foi executado e por isso foi removido. Logo, banco,
modelos e esta migration descrevem a mesma estrutura.

Em bancos que JÁ têm estas tabelas, não rode `upgrade`: use `alembic stamp 0001`
para marcá-la como aplicada. O upgrade falharia no primeiro CREATE TABLE. Como a
migration confere com o banco real, o stamp é uma afirmação verdadeira.

Revision ID: 0001
Revises:
Create Date: 2026-07-16
"""

from collections.abc import Sequence

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision: str = "0001"
down_revision: str | Sequence[str] | None = None
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "carteiras",
        sa.Column(
            "id",
            postgresql.UUID(as_uuid=True),
            server_default=sa.text("gen_random_uuid()"),
            nullable=False,
        ),
        # Sem FK para auth.users: aquele schema é do Supabase e não existe num
        # Postgres limpo — é o que impede o sql/schema.sql de rodar nos testes.
        sa.Column("user_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("nome", sa.String(length=120), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_carteiras_user_id", "carteiras", ["user_id"])

    op.create_table(
        "transacoes",
        sa.Column(
            "id",
            postgresql.UUID(as_uuid=True),
            server_default=sa.text("gen_random_uuid()"),
            nullable=False,
        ),
        sa.Column("carteira_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("ticker", sa.String(length=20), nullable=False),
        sa.Column("nome", sa.String(length=120), nullable=True),
        sa.Column("tipo_ativo", sa.String(length=40), nullable=True),
        # Sem CHECK: o modelo não o declara. Quem barra valores fora de
        # compra/venda hoje é o Literal do Pydantic, na borda.
        sa.Column("operacao", sa.String(length=10), nullable=False),
        sa.Column("quantidade", sa.Numeric(precision=20, scale=8), nullable=False),
        sa.Column("preco_unit", sa.Numeric(precision=20, scale=4), nullable=False),
        # `outros_custos` e `fonte` têm default no Python (models.py), não no
        # banco — por isso não há server_default aqui.
        sa.Column("outros_custos", sa.Numeric(precision=20, scale=4), nullable=False),
        sa.Column("data", sa.Date(), nullable=False),
        sa.Column("fonte", sa.String(length=40), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(["carteira_id"], ["carteiras.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_transacoes_carteira_id", "transacoes", ["carteira_id"])

    op.create_table(
        "proventos",
        sa.Column(
            "id",
            postgresql.UUID(as_uuid=True),
            server_default=sa.text("gen_random_uuid()"),
            nullable=False,
        ),
        sa.Column("carteira_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("ticker", sa.String(length=20), nullable=False),
        sa.Column("tipo_provento", sa.String(length=40), nullable=False),
        sa.Column("data_com", sa.Date(), nullable=True),
        sa.Column("data_pagamento", sa.Date(), nullable=True),
        sa.Column("valor_por_acao", sa.Numeric(precision=20, scale=6), nullable=False),
        # Calculados na Data COM: nulos quando não havia posição/PM na data.
        sa.Column("quantidade", sa.Numeric(precision=20, scale=8), nullable=True),
        sa.Column("pm_historico", sa.Numeric(precision=20, scale=4), nullable=True),
        sa.Column("valor_recebido", sa.Numeric(precision=20, scale=2), nullable=True),
        sa.Column("yoc_evento", sa.Numeric(precision=20, scale=4), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(["carteira_id"], ["carteiras.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_proventos_carteira_id", "proventos", ["carteira_id"])


def downgrade() -> None:
    # Ordem inversa: os filhos antes do pai, por causa das FKs.
    op.drop_index("ix_proventos_carteira_id", table_name="proventos")
    op.drop_table("proventos")
    op.drop_index("ix_transacoes_carteira_id", table_name="transacoes")
    op.drop_table("transacoes")
    op.drop_index("ix_carteiras_user_id", table_name="carteiras")
    op.drop_table("carteiras")
