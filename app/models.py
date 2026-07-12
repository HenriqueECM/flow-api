import uuid
from datetime import date, datetime
from decimal import Decimal

from sqlalchemy import Date, DateTime, ForeignKey, Numeric, String, func, text
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
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
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
