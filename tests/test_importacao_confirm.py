"""Testes do endpoint de confirmação do import de Ativos.

Não há harness de DB assíncrono no projeto (os modelos usam tipos Postgres);
então exercitamos a corrotina do endpoint diretamente via `asyncio.run`, com uma
sessão falsa que devolve as transações "já existentes no banco".
"""

import asyncio
from datetime import date
from decimal import Decimal
from uuid import uuid4

from app.models import Transacao
from app.routers.importacao import confirm_import_ativos
from app.schemas import ImportConfirmIn, ReviewRow


class _FakeScalars:
    def __init__(self, rows):
        self._rows = rows

    def all(self):
        return self._rows


class _FakeResult:
    def __init__(self, rows):
        self._rows = rows

    def scalars(self):
        return _FakeScalars(self._rows)


class _FakeSession:
    """Mínimo do AsyncSession usado por confirm_import_ativos."""

    def __init__(self, existentes):
        self._existentes = existentes
        self.added: list[Transacao] = []
        self.commits = 0

    async def execute(self, _stmt):
        return _FakeResult(self._existentes)

    def add(self, obj):
        self.added.append(obj)

    async def commit(self):
        self.commits += 1


class _FakeCarteira:
    def __init__(self):
        self.id = uuid4()


def _existente(ticker, operacao, qtd, preco, d):
    return Transacao(
        ticker=ticker,
        operacao=operacao,
        quantidade=Decimal(str(qtd)),
        preco_unit=Decimal(str(preco)),
        outros_custos=Decimal(0),
        data=d,
    )


def _row(ativo, tipo, qtd, preco, data_iso):
    return ReviewRow(
        status="valido",
        ativo=ativo,
        qtde=qtd,
        tipo=tipo,
        preco_medio=preco,
        data=data_iso,
    )


def test_confirm_pula_duplicata_do_banco_e_persiste_a_nova():
    # Banco já tem PETR4 compra 100 @ 30 em 05/01/2024.
    existentes = [_existente("PETR4", "compra", 100, 30, date(2024, 1, 5))]
    session = _FakeSession(existentes)

    payload = ImportConfirmIn(
        rows=[
            # Idêntica à existente → deve virar falha (duplicata), não persistir.
            _row("PETR4", "Compra", 100.0, 30.0, "2024-01-05"),
            # Nova/diferente → deve ser persistida normalmente.
            _row("VALE3", "Compra", 50.0, 60.0, "2024-02-01"),
        ]
    )

    res = asyncio.run(
        confirm_import_ativos(payload=payload, carteira=_FakeCarteira(), db=session)
    )

    # Só a nova foi criada.
    assert res.criadas == 1
    assert session.commits == 1
    assert [tx.ticker for tx in session.added] == ["VALE3"]

    # A duplicata aparece em falhas com o motivo correto.
    assert len(res.falhas) == 1
    falha = res.falhas[0]
    assert falha.ativo == "PETR4"
    assert falha.motivo == (
        "Possível duplicata — já existe uma transação idêntica "
        "(mesmo ticker, data, quantidade e preço) nesta carteira."
    )


def test_confirm_duplicata_exige_correspondencia_exata():
    # Existente PETR4 compra 100 @ 30. Uma venda no mesmo dia/qtd/preço NÃO é
    # duplicata (operação difere) e uma compra com preço diferente também não.
    existentes = [_existente("PETR4", "compra", 100, 30, date(2024, 1, 5))]
    session = _FakeSession(existentes)

    payload = ImportConfirmIn(
        rows=[
            _row("PETR4", "Venda", 100.0, 30.0, "2024-01-05"),  # operação difere
            _row("PETR4", "Compra", 100.0, 31.0, "2024-01-05"),  # preço difere
        ]
    )

    res = asyncio.run(
        confirm_import_ativos(payload=payload, carteira=_FakeCarteira(), db=session)
    )

    # Nenhuma é duplicata. (A venda de 100 cabe na posição de 100 do banco.)
    assert res.criadas == 2
    assert res.falhas == []
    assert {tx.ticker for tx in session.added} == {"PETR4"}
    assert len(session.added) == 2


def test_confirm_nao_trata_linhas_iguais_do_mesmo_lote_como_duplicata():
    # Sem nada no banco: duas compras idênticas no mesmo lote são legítimas.
    session = _FakeSession([])

    payload = ImportConfirmIn(
        rows=[
            _row("ITUB4", "Compra", 100.0, 32.0, "2024-03-01"),
            _row("ITUB4", "Compra", 100.0, 32.0, "2024-03-01"),
        ]
    )

    res = asyncio.run(
        confirm_import_ativos(payload=payload, carteira=_FakeCarteira(), db=session)
    )

    assert res.criadas == 2
    assert res.falhas == []
    assert len(session.added) == 2
