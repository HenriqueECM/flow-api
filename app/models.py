import uuid
from datetime import date, datetime
from decimal import Decimal

from sqlalchemy import (
    Boolean,
    CheckConstraint,
    Date,
    DateTime,
    ForeignKey,
    Index,
    Numeric,
    String,
    func,
    text,
)
from sqlalchemy.dialects.postgresql import UUID as PGUUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.core.db import Base


class Carteira(Base):
    __tablename__ = "carteiras"

    id: Mapped[uuid.UUID] = mapped_column(
        PGUUID(as_uuid=True), primary_key=True, server_default=text("gen_random_uuid()")
    )
    # Dono da carteira (auth.users.id do Supabase).
    user_id: Mapped[uuid.UUID] = mapped_column(PGUUID(as_uuid=True), index=True)
    nome: Mapped[str] = mapped_column(String(120))
    # A carteira que /ativa devolve. `server_default` (e não `default=`, como as
    # demais colunas): quem grava aqui também é a migration, ao adicionar a
    # coluna nas linhas que já existem.
    is_default: Mapped[bool] = mapped_column(
        Boolean, nullable=False, server_default=text("false")
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )

    __table_args__ = (
        # No máximo uma padrão por usuário — a regra fica no banco, não na
        # aplicação, e por isso vale mesmo com várias instâncias da API.
        #
        # Índice PARCIAL: o `WHERE is_default` restringe a unicidade às linhas
        # verdadeiras. Um UNIQUE(user_id, is_default) permitiria só uma carteira
        # NÃO-padrão por usuário — o oposto do que queremos.
        #
        # Fica no modelo (e não só na migration) porque o harness cria o schema
        # de teste com `create_all`: sem isto, a constraint não existiria nos
        # testes e o tratamento de conflito nunca seria exercitado.
        Index(
            "uq_carteiras_padrao_por_usuario",
            "user_id",
            unique=True,
            postgresql_where=text("is_default"),
        ),
    )

    transacoes: Mapped[list["Transacao"]] = relationship(
        back_populates="carteira", cascade="all, delete-orphan"
    )
    proventos: Mapped[list["Provento"]] = relationship(
        back_populates="carteira", cascade="all, delete-orphan"
    )


class Transacao(Base):
    """Compra/venda de um ativo (base para calcular as posições)."""

    __tablename__ = "transacoes"

    id: Mapped[uuid.UUID] = mapped_column(
        PGUUID(as_uuid=True), primary_key=True, server_default=text("gen_random_uuid()")
    )
    carteira_id: Mapped[uuid.UUID] = mapped_column(
        PGUUID(as_uuid=True), ForeignKey("carteiras.id", ondelete="CASCADE"), index=True
    )
    ticker: Mapped[str] = mapped_column(String(20))
    nome: Mapped[str | None] = mapped_column(String(120))
    tipo_ativo: Mapped[str | None] = mapped_column(String(40))
    operacao: Mapped[str] = mapped_column(String(10))  # 'compra' | 'venda'
    quantidade: Mapped[Decimal] = mapped_column(Numeric(20, 8))
    preco_unit: Mapped[Decimal] = mapped_column(Numeric(20, 4))
    outros_custos: Mapped[Decimal] = mapped_column(Numeric(20, 4), default=0)
    data: Mapped[date] = mapped_column(Date)
    fonte: Mapped[str] = mapped_column(String(40), default="Manual")
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )

    __table_args__ = (
        # Última linha de defesa da regra que o sistema inteiro pressupõe. O
        # motor de posição faz `if operacao == "compra" ... else venda`: um
        # terceiro valor não daria erro, viraria VENDA e corromperia o cálculo
        # de posição e PM em silêncio.
        #
        # Hoje quem barra é só o Literal do Pydantic, na borda — nada protege
        # contra INSERT por SQL, script de importação ou um endpoint futuro que
        # não passe pelo schema.
        CheckConstraint(
            "operacao in ('compra', 'venda')", name="ck_transacoes_operacao"
        ),
    )

    carteira: Mapped[Carteira] = relationship(back_populates="transacoes")


class Provento(Base):
    """Dividendo, JCP, rendimento etc. recebido por um ativo."""

    __tablename__ = "proventos"

    id: Mapped[uuid.UUID] = mapped_column(
        PGUUID(as_uuid=True), primary_key=True, server_default=text("gen_random_uuid()")
    )
    carteira_id: Mapped[uuid.UUID] = mapped_column(
        PGUUID(as_uuid=True), ForeignKey("carteiras.id", ondelete="CASCADE"), index=True
    )
    ticker: Mapped[str] = mapped_column(String(20))
    tipo_provento: Mapped[str] = mapped_column(String(40))
    data_com: Mapped[date | None] = mapped_column(Date)
    data_pagamento: Mapped[date | None] = mapped_column(Date)
    valor_por_acao: Mapped[Decimal] = mapped_column(Numeric(20, 6))
    # Campos calculados na Data COM via motor de posição (podem ser nulos se
    # não havia posição/PM sincronizado na data).
    quantidade: Mapped[Decimal | None] = mapped_column(Numeric(20, 8))
    pm_historico: Mapped[Decimal | None] = mapped_column(Numeric(20, 4))
    valor_recebido: Mapped[Decimal | None] = mapped_column(Numeric(20, 2))
    yoc_evento: Mapped[Decimal | None] = mapped_column(Numeric(20, 4))
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )

    carteira: Mapped[Carteira] = relationship(back_populates="proventos")
