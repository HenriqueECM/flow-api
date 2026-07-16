from collections import defaultdict
from datetime import date
from decimal import Decimal

from fastapi import APIRouter, Depends, File, HTTPException, UploadFile, status
from fastapi.concurrency import run_in_threadpool
from pydantic import ValidationError
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.db import get_db
from app.deps import get_owned_carteira
from app.models import Carteira, Transacao
from app.schemas import (
    ImportAtivosPreviewOut,
    ImportConfirmIn,
    ImportConfirmResultOut,
    ImportFalha,
    ImportRevalidateIn,
    ReviewRow,
    TransacaoCreate,
)
from app.services.import_movimentacao import (
    detectar_duplicatas_no_banco,
    parse_movimentacao_ativos,
    revalidar_lote,
    validar_posicao_lote,
)

router = APIRouter(prefix="/carteiras/{carteira_id}/import", tags=["import"])


@router.post("/ativos/preview", response_model=ImportAtivosPreviewOut)
async def preview_import_ativos(
    carteira: Carteira = Depends(get_owned_carteira),
    file: UploadFile = File(...),
) -> ImportAtivosPreviewOut:
    """Recebe a planilha de Movimentação (.xlsx) e devolve as linhas
    classificadas (valido/erro/ignorado) + resumo — sem persistir nada."""
    conteudo = await file.read()
    try:
        rows, summary = await run_in_threadpool(parse_movimentacao_ativos, conteudo)
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc))
    return ImportAtivosPreviewOut(rows=rows, summary=summary)


@router.post("/ativos/revalidate", response_model=ImportAtivosPreviewOut)
async def revalidate_import_ativos(
    payload: ImportRevalidateIn,
    carteira: Carteira = Depends(get_owned_carteira),
) -> ImportAtivosPreviewOut:
    """Reclassifica a lista COMPLETA de linhas (com as correções manuais do
    usuário) sem reenviar o arquivo. Recalcula status/motivo e a posição
    acumulada do zero, em sequência — então corrigir uma venda pode reabilitar
    automaticamente linhas seguintes do mesmo ticker."""
    rows, summary = revalidar_lote(payload.rows)
    return ImportAtivosPreviewOut(rows=rows, summary=summary)


def _row_para_transacao(row: ReviewRow) -> TransacaoCreate:
    """Mapeia uma ReviewRow confirmada para o payload de criação de transação
    (mesmas validações do endpoint manual)."""
    if not row.data:
        raise ValueError("Linha sem data válida.")
    operacao = "compra" if row.tipo.strip().lower().startswith("compra") else "venda"
    return TransacaoCreate(
        ticker=row.ativo,
        operacao=operacao,
        quantidade=Decimal(str(row.qtde)) if row.qtde is not None else Decimal(0),
        preco_unit=Decimal(str(row.preco_medio)),
        outros_custos=Decimal(0),
        data=date.fromisoformat(row.data),
        fonte="Importação B3",
    )


@router.post("/ativos/confirm", response_model=ImportConfirmResultOut)
async def confirm_import_ativos(
    payload: ImportConfirmIn,
    carteira: Carteira = Depends(get_owned_carteira),
    db: AsyncSession = Depends(get_db),
) -> ImportConfirmResultOut:
    """Persiste as linhas confirmadas pelo frontend como transações.

    Valida posição também aqui: uma venda que exceda o disponível (transações
    já no banco + linhas anteriores do mesmo lote, em ordem) falha só naquela
    linha, sem impedir as demais. Retorna quantas foram criadas e as falhas."""
    falhas: list[ImportFalha] = []

    # Mapeia cada linha para o payload de transação (validações do manual).
    # Linhas com erro de validação nem entram na checagem de posição.
    mapeadas: list[tuple[ReviewRow, TransacaoCreate]] = []
    for row in payload.rows:
        try:
            create = _row_para_transacao(row)
        except (ValidationError, ValueError) as exc:
            falhas.append(ImportFalha(ativo=row.ativo, motivo=str(exc)))
            continue
        mapeadas.append((row, create))

    # Transações já existentes no banco (base para duplicatas e posição).
    result = await db.execute(
        select(Transacao).where(Transacao.carteira_id == carteira.id)
    )
    existentes = list(result.scalars().all())

    # Duplicatas: linha idêntica a uma transação já persistida (mesmo ticker,
    # data, quantidade, preço e operação) não é reimportada. A checagem é só
    # contra o banco — linhas iguais dentro deste mesmo lote são legítimas.
    itens_dup = [
        (c.ticker, c.data, c.quantidade, c.preco_unit, c.operacao) for _, c in mapeadas
    ]
    duplicadas = detectar_duplicatas_no_banco(existentes, itens_dup)
    restantes: list[tuple[ReviewRow, TransacaoCreate]] = []
    for (row, create), eh_duplicata in zip(mapeadas, duplicadas):
        if eh_duplicata:
            falhas.append(
                ImportFalha(
                    ativo=row.ativo,
                    motivo=(
                        "Possível duplicata — já existe uma transação idêntica "
                        "(mesmo ticker, data, quantidade e preço) nesta carteira."
                    ),
                )
            )
            continue
        restantes.append((row, create))

    # Posição líquida inicial por ticker (transações já existentes no banco).
    # Duplicatas ficam de fora do lote para não distorcer a posição — o seu
    # efeito já está contabilizado nas `existentes`.
    posicao: dict[str, Decimal] = defaultdict(lambda: Decimal(0))
    for tx in existentes:
        sinal = Decimal(1) if tx.operacao == "compra" else Decimal(-1)
        posicao[tx.ticker.upper()] += sinal * tx.quantidade

    # Valida o lote em ordem contra a posição disponível.
    itens = [(c.ticker.upper(), c.operacao, c.quantidade) for _, c in restantes]
    motivos = validar_posicao_lote(posicao, itens)

    novas: list[Transacao] = []
    for (row, create), motivo in zip(restantes, motivos):
        if motivo is not None:
            falhas.append(ImportFalha(ativo=row.ativo, motivo=motivo))
            continue
        novas.append(Transacao(carteira_id=carteira.id, **create.model_dump()))

    for transacao in novas:
        db.add(transacao)
    if novas:
        await db.commit()

    return ImportConfirmResultOut(criadas=len(novas), falhas=falhas)
