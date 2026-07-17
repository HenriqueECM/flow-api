"""Parser da planilha "Movimentação" da B3 (compra/venda de ativos).

Lê o export .xlsx, classifica cada linha (valido/erro/ignorado) seguindo o
contrato ReviewRow do frontend e devolve também o resumo por status. Não
persiste nada — é a etapa de preview/revisão.
"""

import re
from collections import Counter
from dataclasses import dataclass
from datetime import date
from decimal import Decimal, InvalidOperation
from io import BytesIO

import pandas as pd

from app.models import Transacao
from app.schemas import ReviewRow, ReviewSummary

# "FII" como palavra isolada (não substring), case-insensitive.
_FII_RE = re.compile(r"\bFII\b", re.IGNORECASE)

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
# Movimentações que afetam a quantidade sem custo (não são compra/venda).
MOV_BONIFICACAO = "Bonificação em Ativos"  # adiciona à posição (dilui o PM)
MOV_FRACAO = "Fração em Ativos"  # subtrai da posição (ajuste da fração)

# Como cada linha deve ser tratada pelo motor de posição (_classificar_lote).
TRAT_POSICAO = "posicao"  # entra no motor: compra soma, venda valida/subtrai
TRAT_IGNORADO = "ignorado"  # mantém-se ignorada, não toca a posição
TRAT_ERRO = "erro"  # erro fixo (ex.: ticker não parseável), não toca a posição


@dataclass
class RegistroNormalizado:
    """Registro já extraído/normalizado, pronto para o motor de posição.

    Separa a etapa de *parse* (ler o Excel ou reconstruir de ReviewRows) da
    etapa de *classificação* (decidir valido/erro por linha mantendo a posição
    acumulada). O campo ``tratamento`` diz como a linha entra no motor.
    """

    ativo: str
    qtde: float | None
    preco: float | None
    valor: float | None
    data_iso: str | None
    tipo: str  # "Compra" | "Venda" | ""
    tratamento: str  # TRAT_POSICAO | TRAT_IGNORADO | TRAT_ERRO
    operacao: str | None = None  # "compra" | "venda" quando tratamento == posicao
    motivo: str | None = None  # motivo pré-definido p/ ignorado/erro


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
    """Lê o DataFrame da planilha, normaliza as linhas e as classifica."""
    df = df.rename(columns=lambda c: str(c).strip())
    faltando = [c for c in _COLUNAS_ESPERADAS if c not in df.columns]
    if faltando:
        raise ValueError("Colunas ausentes na planilha: " + ", ".join(faltando))

    # Extrai os campos brutos de cada linha preservando a ordem do arquivo.
    brutos = []
    for _, r in df.iterrows():
        data_obj, data_iso = _parse_data(r[COL_DATA])
        brutos.append(
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
    datas = [b["data_obj"] for b in brutos if b["data_obj"] is not None]
    if len(datas) >= 2 and datas[0] > datas[-1]:
        brutos.reverse()

    registros = [_normalizar(b) for b in brutos]
    return _classificar_lote(registros)


def _normalizar(reg: dict) -> RegistroNormalizado:
    """Extrai o registro normalizado de uma linha bruta da planilha, decidindo
    apenas o *tratamento* (posição/ignorado/erro) — sem tocar a posição."""
    mov = reg["movimentacao"]
    produto = reg["produto"]
    ticker = produto.split(" - ")[0].strip().upper() if produto else ""

    def _reg(*, ativo, tipo, tratamento, operacao=None, motivo=None):
        return RegistroNormalizado(
            ativo=ativo,
            qtde=reg["quantidade"],
            preco=reg["preco"],
            valor=reg["valor"],
            data_iso=reg["data_iso"],
            tipo=tipo,
            tratamento=tratamento,
            operacao=operacao,
            motivo=motivo,
        )

    # FIIs ainda não são suportados: se a descrição (parte após " - ") contém
    # "FII" como palavra isolada, ignora a linha sem tocar na posição (isolado
    # de outros tickers).
    descricao = produto.split(" - ", 1)[1] if " - " in produto else ""
    if _FII_RE.search(descricao):
        return _reg(
            ativo=ticker or produto,
            tipo="",
            tratamento=TRAT_IGNORADO,
            motivo=(
                "Fundo Imobiliário (FII) — suporte ainda não implementado "
                "neste import"
            ),
        )

    # Bonificação / Fração: afetam a quantidade sem custo associado (Preço/Valor
    # vêm vazios). Entram na sequência cronológica do motor de posição.
    if mov in (MOV_BONIFICACAO, MOV_FRACAO):
        if not ticker:
            return _reg(
                ativo="",
                tipo="",
                tratamento=TRAT_ERRO,
                motivo=(
                    "Não foi possível identificar o ticker no campo Produto: "
                    f"'{produto}'"
                ),
            )
        # Bonificação adiciona (mapeada como Compra a custo 0, que dilui o PM);
        # fração subtrai (Venda, sem custo).
        if mov == MOV_BONIFICACAO:
            return _reg(
                ativo=ticker, tipo="Compra", tratamento=TRAT_POSICAO, operacao="compra"
            )
        return _reg(
            ativo=ticker, tipo="Venda", tratamento=TRAT_POSICAO, operacao="venda"
        )

    # Só "Transferência - Liquidação" é compra/venda; o resto é ignorado.
    if mov != MOV_LIQUIDACAO:
        return _reg(
            ativo=ticker or produto,
            tipo="",
            tratamento=TRAT_IGNORADO,
            motivo=(
                f"Tipo de movimentação '{mov}' não é compra/venda — "
                "não será importado"
            ),
        )

    operacao = "compra" if reg["entrada_saida"] == "Credito" else "venda"
    tipo = "Compra" if operacao == "compra" else "Venda"
    if not ticker:
        return _reg(
            ativo="",
            tipo=tipo,
            tratamento=TRAT_ERRO,
            motivo=(
                "Não foi possível identificar o ticker no campo Produto: "
                f"'{produto}'"
            ),
        )
    return _reg(ativo=ticker, tipo=tipo, tratamento=TRAT_POSICAO, operacao=operacao)


def _classificar_lote(
    registros: list[RegistroNormalizado],
) -> tuple[list[ReviewRow], ReviewSummary]:
    """Classifica os registros em sequência, mantendo a posição acumulada por
    ticker (com suporte a ciclos). Linhas ``ignorado``/``erro`` não alteram a
    posição; apenas compras/vendas movem o acumulado e vendas que excedem o
    disponível na data viram ``erro``."""
    posicao: dict[str, Decimal] = {}
    rows = [_classificar_registro(reg, posicao) for reg in registros]

    contagem = Counter(row.status for row in rows)
    summary = ReviewSummary(
        total=len(rows),
        validas=contagem.get("valido", 0),
        alertas=contagem.get("alerta", 0),
        erros=contagem.get("erro", 0),
        ignoradas=contagem.get("ignorado", 0),
    )
    return rows, summary


def _classificar_registro(
    reg: RegistroNormalizado, posicao: dict[str, Decimal]
) -> ReviewRow:
    if reg.tratamento == TRAT_IGNORADO:
        return _row(
            "ignorado",
            ativo=reg.ativo,
            qtde=reg.qtde,
            preco=reg.preco,
            valor=reg.valor,
            tipo="",
            data_iso=reg.data_iso,
            motivo=reg.motivo,
        )

    if reg.tratamento == TRAT_ERRO:
        return _row(
            "erro",
            ativo=reg.ativo,
            qtde=reg.qtde,
            preco=reg.preco,
            valor=reg.valor,
            tipo=reg.tipo,
            data_iso=reg.data_iso,
            motivo=reg.motivo,
        )

    ticker = reg.ativo
    qtd = _decimal(reg.qtde)
    if reg.operacao == "venda":
        disponivel = posicao.get(ticker, Decimal(0))
        if qtd > disponivel:
            return _row(
                "erro",
                ativo=ticker,
                qtde=reg.qtde,
                preco=reg.preco,
                valor=reg.valor,
                tipo=reg.tipo,
                data_iso=reg.data_iso,
                motivo=(
                    f"Venda de {reg.qtde} unidades de {ticker} "
                    "excede a posição disponível na data"
                ),
            )
        posicao[ticker] = disponivel - qtd
    else:  # compra válida
        posicao[ticker] = posicao.get(ticker, Decimal(0)) + qtd

    return _row(
        "valido",
        ativo=ticker,
        qtde=reg.qtde,
        preco=reg.preco,
        valor=reg.valor,
        tipo=reg.tipo,
        data_iso=reg.data_iso,
        motivo=None,
    )


def _registro_de_review_row(row: ReviewRow) -> RegistroNormalizado:
    """Reconstrói um RegistroNormalizado a partir de uma ReviewRow (possivelmente
    editada pelo usuário) para reclassificação. Linhas ``ignorado`` mantêm-se
    como estão; as demais entram no motor de posição pelo seu ``tipo``."""
    comum = dict(
        qtde=row.qtde,
        preco=row.preco_medio,
        valor=row.valor_total,
        data_iso=row.data,
    )

    if row.status == "ignorado":
        return RegistroNormalizado(
            ativo=row.ativo,
            tipo="",
            tratamento=TRAT_IGNORADO,
            motivo=row.motivo,
            **comum,
        )

    ticker = row.ativo.strip().upper()
    operacao = "compra" if row.tipo.strip().lower().startswith("compra") else "venda"
    if not ticker:
        return RegistroNormalizado(
            ativo="",
            tipo=row.tipo,
            tratamento=TRAT_ERRO,
            motivo=row.motivo or "Não foi possível identificar o ticker.",
            **comum,
        )
    return RegistroNormalizado(
        ativo=ticker,
        tipo=row.tipo,
        tratamento=TRAT_POSICAO,
        operacao=operacao,
        **comum,
    )


def revalidar_lote(
    rows: list[ReviewRow],
) -> tuple[list[ReviewRow], ReviewSummary]:
    """Reclassifica do zero a lista COMPLETA de ReviewRows (incluindo linhas
    editadas manualmente), recalculando a posição acumulada em sequência. Uma
    correção numa venda pode reabilitar linhas seguintes do mesmo ticker que
    antes davam erro por causa do acumulado incorreto."""
    registros = [_registro_de_review_row(row) for row in rows]
    return _classificar_lote(registros)


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


def _chave_transacao(
    ticker: str, data: date, quantidade: Decimal, preco_unit: Decimal, operacao: str
) -> tuple:
    """Identidade exata de uma transação, para detectar duplicatas.

    Decimais iguais em valor (ex.: 100 e 100.00) comparam/hasham igual, então a
    chave casa independente de zeros à direita vindos do banco vs. do lote.
    """
    return (ticker.strip().upper(), data, quantidade, preco_unit, operacao)


def detectar_duplicatas_no_banco(
    existentes: list[Transacao],
    itens: list[tuple[str, date, Decimal, Decimal, str]],
) -> list[bool]:
    """Para cada item do lote, indica se já existe uma transação idêntica entre
    `existentes` — mesmo ticker, data, quantidade, preço unitário e operação.

    A comparação é feita apenas contra o que já está no banco: itens iguais
    dentro do próprio lote NÃO se anulam entre si (duas compras idênticas no
    mesmo dia são raras, mas legítimas — não são duplicata uma da outra).
    """
    chaves = {
        _chave_transacao(tx.ticker, tx.data, tx.quantidade, tx.preco_unit, tx.operacao)
        for tx in existentes
    }
    return [
        _chave_transacao(ticker, data, quantidade, preco_unit, operacao) in chaves
        for ticker, data, quantidade, preco_unit, operacao in itens
    ]


def parse_movimentacao_ativos(conteudo: bytes) -> tuple[list[ReviewRow], ReviewSummary]:
    """Lê o .xlsx (bytes) e classifica as linhas."""
    try:
        df = pd.read_excel(BytesIO(conteudo))
    except Exception as exc:  # noqa: BLE001 — erro de leitura vira 400 no router
        raise ValueError(f"Não foi possível ler a planilha: {exc}") from exc
    return parse_dataframe(df)
