from datetime import date, datetime
from decimal import Decimal
from typing import Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field

Operacao = Literal["compra", "venda"]


# ── Carteiras ────────────────────────────────────────────────────────────────
class CarteiraCreate(BaseModel):
    nome: str = Field(min_length=1, max_length=120)


class CarteiraOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    nome: str
    created_at: datetime


# ── Transações ───────────────────────────────────────────────────────────────
class TransacaoCreate(BaseModel):
    ticker: str = Field(min_length=1, max_length=20)
    nome: str | None = None
    tipo_ativo: str | None = None
    operacao: Operacao
    quantidade: Decimal = Field(gt=0)
    preco_unit: Decimal = Field(ge=0)
    outros_custos: Decimal = Field(default=Decimal(0), ge=0)
    data: date
    fonte: str = "Manual"


class TransacaoOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    carteira_id: UUID
    ticker: str
    nome: str | None
    tipo_ativo: str | None
    operacao: Operacao
    quantidade: Decimal
    preco_unit: Decimal
    outros_custos: Decimal
    data: date
    fonte: str
    created_at: datetime


# ── Proventos ────────────────────────────────────────────────────────────────
class ProventoCreate(BaseModel):
    ticker: str = Field(min_length=1, max_length=20)
    tipo_provento: str
    data_com: date | None = None
    data_pagamento: date | None = None
    valor_por_acao: Decimal = Field(ge=0)


class ProventoOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    carteira_id: UUID
    ticker: str
    tipo_provento: str
    data_com: date | None
    data_pagamento: date | None
    valor_por_acao: Decimal
    # Calculados na Data COM (nulos se não havia posição/PM na data).
    quantidade: Decimal | None
    pm_historico: Decimal | None
    valor_recebido: Decimal | None
    yoc_evento: Decimal | None
    created_at: datetime


class ProventoPreviewOut(BaseModel):
    """Preview dos campos calculados (read-only, sem persistir)."""

    quantidade: Decimal | None = None
    pm_historico: Decimal | None = None
    valor_recebido: Decimal | None = None
    yoc_evento: Decimal | None = None


# ── Posição consolidada (calculada + cotação atual) ──────────────────────────
class PosicaoOut(BaseModel):
    ticker: str
    nome: str
    quantidade: Decimal
    pm_historico: Decimal
    # Cotação (brapi.dev). None quando não há cotação disponível para o ticker.
    preco_atual: Decimal | None = None
    variacao_percent: float | None = None
    valor_total: Decimal | None = None
    lucro: Decimal | None = None
