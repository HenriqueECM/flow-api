"""carteira padrao por usuario

Adiciona `is_default` e garante, no banco, no máximo uma carteira padrão por
usuário. Fecha a corrida do GET /carteiras/ativa: ele fazia SELECT-e-depois-
INSERT sem lock nem constraint, então duas chamadas concorrentes de um usuário
sem carteira criavam duas "Minha Carteira".

Três passos, nesta ordem — e a ordem é parte da correção:

1. ADD COLUMN
2. UPDATE dos dados existentes
3. CREATE UNIQUE INDEX

O índice vem por último de propósito: se o UPDATE produzisse duas padrões para
algum usuário, a criação do índice falha e a migration inteira aborta. A
constraint é o teste da própria migração de dados.

Revision ID: 0002
Revises: 0001
Create Date: 2026-07-17
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "0002"
down_revision: str | Sequence[str] | None = "0001"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # Um único ADD COLUMN com NOT NULL DEFAULT, e não o padrão em etapas
    # (add nullable → UPDATE → SET NOT NULL → SET DEFAULT). Desde o Postgres 11,
    # adicionar coluna com default não-volátil NÃO reescreve a tabela: o valor
    # fica no catálogo e as linhas existentes nem são tocadas. O caminho em
    # etapas seria pior aqui — o UPDATE reescreveria toda linha (bloat, lock
    # longo) e o SET NOT NULL faria full scan com ACCESS EXCLUSIVE.
    op.add_column(
        "carteiras",
        sa.Column(
            "is_default",
            sa.Boolean(),
            nullable=False,
            server_default=sa.text("false"),
        ),
    )

    # A mais antiga de cada usuário vira a padrão. É exatamente o que /ativa
    # devolvia antes (ORDER BY created_at LIMIT 1), então nenhum usuário vê o
    # app abrir uma carteira diferente da de ontem.
    #
    # `id` desempata created_at idênticos: sem ele o DISTINCT ON escolheria
    # arbitrariamente e a migration não seria reproduzível.
    op.execute("""
        UPDATE carteiras SET is_default = true
        WHERE id IN (
            SELECT DISTINCT ON (user_id) id
            FROM carteiras
            ORDER BY user_id, created_at, id
        )
        """)

    # Índice PARCIAL: a unicidade vale só entre as linhas com is_default = true.
    # Um UNIQUE(user_id, is_default) permitiria apenas uma carteira NÃO-padrão
    # por usuário, que é o contrário do desejado.
    op.create_index(
        "uq_carteiras_padrao_por_usuario",
        "carteiras",
        ["user_id"],
        unique=True,
        postgresql_where=sa.text("is_default"),
    )


def downgrade() -> None:
    # Reversível. O comportamento volta ao anterior — /ativa passa a devolver a
    # mais antiga —, que é o mesmo critério que o upgrade usou para escolher a
    # padrão. A perda: se alguém tiver trocado a padrão para uma carteira que
    # não é a mais antiga, essa escolha não sobrevive ao downgrade.
    op.drop_index("uq_carteiras_padrao_por_usuario", table_name="carteiras")
    op.drop_column("carteiras", "is_default")
