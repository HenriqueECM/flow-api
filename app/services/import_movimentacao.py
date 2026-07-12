"""Parser da planilha "Movimentação" da B3 (compra/venda de ativos).

Lê o export .xlsx, classifica cada linha (valido/erro/ignorado) seguindo o
contrato ReviewRow do frontend e devolve também o resumo por status. Não
persiste nada — é a etapa de preview/revisão.
"""

from collections import Counter
from decimal import Decimal, InvalidOperation
from io import BytesIO

import pandas as pd

from app.schemas import ReviewRow, ReviewSummary

# Nomes de coluna do export "Movimentação" da B3.
COL_ENTRADA_SAIDA = "Entrada/Saída"
COL_DATA = "Data"
COL_MOVIMENTACAO = "Movimentação"
COL_PRODUTO = "Produto"
COL_QUANTIDADE = "Quantidade"
COL_PRECO_UNIT = "Preço unitário"
COL_VALOR = "Valor da Operação"

_COLUNAS_ESPERADAS = [
    COL_ENTRADA_SAIDA,
    COL_DATA,
    COL_MOVIMENTACAO,
    COL_PRODUTO,
    COL_QUANTIDADE,
    COL_PRECO_UNIT,
    COL_VALOR,
]

MOV_LIQUIDACAO = "Transferência - Liquidação"


def _texto(valor) -> str:
    return "" if pd.isna(valor) else str(valor).strip()


def _numero(valor) -> float | None:
    if pd.isna(valor):
        return None
    try:
        return float(valor)
    except (TypeError, ValueError):
        return None


def _decimal(valor: float | None) -> Decimal:
    if valor is None:
        return Decimal(0)
    try:
        return Decimal(str(valor))
    except InvalidOperation:
        return Decimal(0)


def _parse_data(valor) -> tuple[object | None, str | None]:
    """Retorna (date, iso-string) ou (None, None) se não parseável."""
    if pd.isna(valor):
        return None, None
    ts = pd.to_datetime(valor, dayfirst=True, errors="coerce")
    if pd.isna(ts):
        return None, None
    d = ts.date()
    return d, d.isoformat()


def _row(status, *, ativo, qtde, preco, valor, tipo, data_iso, motivo) -> ReviewRow:
    return ReviewRow(
        status=status,
        ativo=ativo,
        qtde=qtde,
        preco_medio=preco or 0.0,
        valor_total=valor or 0.0,
        valor=valor or 0.0,
        peso="—",
        tipo=tipo,
        data=data_iso,
        data_com=None,
        motivo=motivo,
    )


def parse_dataframe(df: pd.DataFrame) -> tuple[list[ReviewRow], ReviewSummary]:
    """Classifica cada linha do DataFrame já lido da planilha."""
    df = df.rename(columns=lambda c: str(c).strip())
    faltando = [c for c in _COLUNAS_ESPERADAS if c not in df.columns]
    if faltando:
        raise ValueError(
            "Colunas ausentes na planilha: " + ", ".join(faltando)
        )

    # Extrai os campos de cada linha preservando a ordem do arquivo.
    registros = []
    for _, r in df.iterrows():
        data_obj, data_iso = _parse_data(r[COL_DATA])
        registros.append(
            {
                "entrada_saida": _texto(r[COL_ENTRADA_SAIDA]),
                "movimentacao": _texto(r[COL_MOVIMENTACAO]),
                "produto": _texto(r[COL_PRODUTO]),
                "quantidade": _numero(r[COL_QUANTIDADE]),
                "preco": _numero(r[COL_PRECO_UNIT]),
                "valor": _numero(r[COL_VALOR]),
                "data_obj": data_obj,
                "data_iso": data_iso,
            }
        )

    # A B3 exporta do mais recente para o mais antigo. Precisamos processar do
    # mais antigo para o mais recente (posição/ciclos): reverte se descendente.
    datas = [reg["data_obj"] for reg in registros if reg["data_obj"] is not None]
    if len(datas) >= 2 and datas[0] > datas[-1]:
        registros.reverse()

    posicao: dict[str, Decimal] = {}
    rows: list[ReviewRow] = []
    for reg in registros:
        rows.append(_classificar(reg, posicao))

    contagem = Counter(row.status for row in rows)
    summary = ReviewSummary(
        total=len(rows),
        validas=contagem.get("valido", 0),
        alertas=contagem.get("alerta", 0),
        erros=contagem.get("erro", 0),
        ignoradas=contagem.get("ignorado", 0),
    )
    return rows, summary


def _classificar(reg: dict, posicao: dict[str, Decimal]) -> ReviewRow:
    mov = reg["movimentacao"]
    produto = reg["produto"]
    ticker = produto.split(" - ")[0].strip().upper() if produto else ""

    # Só "Transferência - Liquidação" é compra/venda; o resto é ignorado.
    if mov != MOV_LIQUIDACAO:
        return _row(
            "ignorado",
            ativo=ticker or produto,
            qtde=reg["quantidade"],
            preco=reg["preco"],
            valor=reg["valor"],
            tipo="",
            data_iso=reg["data_iso"],
            motivo=(
                f"Tipo de movimentação '{mov}' não é compra/venda — "
                "não será importado"
            ),
        )

    operacao = "compra" if reg["entrada_saida"] == "Debito" else "venda"
    tipo = "Compra" if operacao == "compra" else "Venda"

    if not ticker:
        return _row(
            "erro",
            ativo="",
            qtde=reg["quantidade"],
            preco=reg["preco"],
            valor=reg["valor"],
            tipo=tipo,
            data_iso=reg["data_iso"],
            motivo=(
                "Não foi possível identificar o ticker no campo Produto: "
                f"'{produto}'"
            ),
        )

    qtd = _decimal(reg["quantidade"])

    if operacao == "venda":
        disponivel = posicao.get(ticker, Decimal(0))
        if qtd > disponivel:
            return _row(
                "erro",
                ativo=ticker,
                qtde=reg["quantidade"],
                preco=reg["preco"],
                valor=reg["valor"],
                tipo=tipo,
                data_iso=reg["data_iso"],
                motivo=(
                    f"Venda de {reg['quantidade']} unidades de {ticker} "
                    "excede a posição disponível na data"
                ),
            )
        posicao[ticker] = disponivel - qtd
    else:  # compra válida
        posicao[ticker] = posicao.get(ticker, Decimal(0)) + qtd

    return _row(
        "valido",
        ativo=ticker,
        qtde=reg["quantidade"],
        preco=reg["preco"],
        valor=reg["valor"],
        tipo=tipo,
        data_iso=reg["data_iso"],
        motivo=None,
    )


def _qtd_str(d: Decimal) -> str:
    """Formata uma quantidade Decimal sem zeros/expoente supérfluos."""
    return format(d.normalize(), "f")


def validar_posicao_lote(
    posicao: dict[str, Decimal],
    itens: list[tuple[str, str, Decimal]],
) -> list[str | None]:
    """Valida vendas de um lote contra a posição disponível, em ordem.

    `posicao` é a posição líquida inicial por ticker (ex.: transações já
    existentes no banco) e é atualizada a cada item que "passa". Para cada item
    `(ticker, operacao, quantidade)` retorna None se cabe, ou a mensagem de erro
    se a venda excede o disponível. Itens que falham não alteram a posição
    (não serão persistidos), sem bloquear os demais.
    """
    motivos: list[str | None] = []
    for ticker, operacao, qtd in itens:
        if operacao == "venda":
            disponivel = posicao.get(ticker, Decimal(0))
            if qtd > disponivel:
                motivos.append(
                    f"Venda de {_qtd_str(qtd)} unidades de {ticker} excede a "
                    f"posição disponível ({_qtd_str(disponivel)})."
                )
                continue
            posicao[ticker] = disponivel - qtd
        else:  # compra
            posicao[ticker] = posicao.get(ticker, Decimal(0)) + qtd
        motivos.append(None)
    return motivos


def parse_movimentacao_ativos(conteudo: bytes) -> tuple[list[ReviewRow], ReviewSummary]:
    """Lê o .xlsx (bytes) e classifica as linhas."""
    try:
        df = pd.read_excel(BytesIO(conteudo))
    except Exception as exc:  # noqa: BLE001 — erro de leitura vira 400 no router
        raise ValueError(f"Não foi possível ler a planilha: {exc}") from exc
    return parse_dataframe(df)
