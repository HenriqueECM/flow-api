"""Retenção de IR do JCP no valor recebido / YoC dos proventos.

Cobre as funções puras do motor e o serializador `_provento_out` do router, que
deriva o valor líquido a partir do bruto persistido (sem recomputar posição).
"""

from datetime import date, datetime
from decimal import Decimal
from uuid import uuid4

import pytest

from app.models import Provento
from app.routers.proventos import _provento_out
from app.services.proventos_engine import (
    eh_jcp,
    liquidar_valor_recebido,
    liquidar_yoc,
)


@pytest.mark.parametrize(
    "tipo,esperado",
    [
        ("JCP", True),
        ("jcp", True),
        ("Juros sobre Capital Próprio", True),
        ("JUROS SOBRE CAPITAL PRÓPRIO", True),
        ("JSCP - JCP", True),
        ("Dividendo", False),
        ("Rendimento", False),
        ("", False),
        (None, False),
    ],
)
def test_eh_jcp_detecta_variantes(tipo, esperado):
    assert eh_jcp(tipo) is esperado


def test_liquidar_valor_recebido_jcp_retem_17_5_pct():
    # 100 bruto → 82,50 líquido (retém 17,5% de IRRF).
    assert liquidar_valor_recebido("JCP", Decimal("100")) == Decimal("82.50")
    assert liquidar_valor_recebido("Juros sobre Capital Próprio", Decimal("200")) == Decimal("165.00")


def test_liquidar_valor_recebido_isentos_inalterados():
    assert liquidar_valor_recebido("Dividendo", Decimal("100")) == Decimal("100")
    assert liquidar_valor_recebido("Rendimento", Decimal("55.55")) == Decimal("55.55")


def test_liquidar_valor_recebido_none():
    assert liquidar_valor_recebido("JCP", None) is None
    assert liquidar_valor_recebido("Dividendo", None) is None


def test_liquidar_yoc_acompanha_a_retencao():
    # YoC líquido reduz na mesma proporção do imposto (× 0,825) só p/ JCP.
    assert liquidar_yoc("JCP", Decimal("10")) == Decimal("8.2500")
    assert liquidar_yoc("Dividendo", Decimal("10")) == Decimal("10")
    assert liquidar_yoc("JCP", None) is None


def _provento(tipo, valor_recebido, yoc):
    """Provento persistido (valores BRUTOS), com os campos que ProventoOut exige."""
    return Provento(
        id=uuid4(),
        carteira_id=uuid4(),
        created_at=datetime(2024, 1, 1),
        ticker="ITSA4",
        tipo_provento=tipo,
        data_com=date(2024, 1, 5),
        data_pagamento=date(2024, 2, 1),
        valor_por_acao=Decimal("1"),
        quantidade=Decimal("100"),
        pm_historico=Decimal("10"),
        valor_recebido=Decimal(str(valor_recebido)),
        yoc_evento=Decimal(str(yoc)),
    )


def test_provento_out_jcp_devolve_liquido():
    # Bruto persistido: 100,00 / YoC 10%. Serialização líquida: 82,50 / 8,25%.
    out = _provento_out(_provento("JCP", "100", "10"))

    assert out.valor_recebido == Decimal("82.50")
    assert out.yoc_evento == Decimal("8.2500")
    # Demais campos passam inalterados (inclusive o PM da Data COM).
    assert out.quantidade == Decimal("100")
    assert out.pm_historico == Decimal("10")
    assert out.valor_por_acao == Decimal("1")


def test_provento_out_dividendo_mantem_bruto():
    out = _provento_out(_provento("Dividendo", "50", "5"))

    assert out.valor_recebido == Decimal("50")
    assert out.yoc_evento == Decimal("5")
