"""check em transacoes.operacao

O banco aceita qualquer texto em `operacao` hoje. A garantia estava prometida no
sql/schema.sql, mas aquele arquivo nunca foi executado (ver 0001) — a introspecção
confirmou: nenhuma constraint do tipo CHECK existe na tabela.

Por que importa mais do que parece: o motor de posição faz
`if operacao == "compra" ... else venda`. Um terceiro valor não daria erro — viraria
VENDA, e corromperia posição e PM em silêncio. Quem barra hoje é apenas o Literal
do Pydantic, na borda; nada protege contra INSERT por SQL, script de importação ou
um endpoint futuro que não passe pelo schema.

NOT VALID + VALIDATE, e não um ADD CONSTRAINT direto: o direto faz full scan
segurando ACCESS EXCLUSIVE, bloqueando leitura e escrita na tabela. O NOT VALID é
instantâneo (só passa a valer para linhas novas) e o VALIDATE varre com
SHARE UPDATE EXCLUSIVE, que não bloqueia leitura nem escrita.

Se houver alguma linha com `operacao` fora do par, o VALIDATE falha e a migration
inteira aborta (o Postgres tem DDL transacional) — o banco volta ao estado
anterior, sem constraint. Confira antes de aplicar:

    select distinct operacao from transacoes;

Revision ID: 0003
Revises: 0002
Create Date: 2026-07-17
"""

from collections.abc import Sequence

from alembic import op

revision: str = "0003"
down_revision: str | Sequence[str] | None = "0002"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

CONSTRAINT = "ck_transacoes_operacao"


def upgrade() -> None:
    op.execute(f"""
        ALTER TABLE transacoes
        ADD CONSTRAINT {CONSTRAINT}
        CHECK (operacao in ('compra', 'venda')) NOT VALID
        """)
    # Valida as linhas que já existem. Separado do ADD para trocar o
    # ACCESS EXCLUSIVE por um lock que não bloqueia o tráfego da tabela.
    op.execute(f"ALTER TABLE transacoes VALIDATE CONSTRAINT {CONSTRAINT}")


def downgrade() -> None:
    op.drop_constraint(CONSTRAINT, "transacoes", type_="check")
