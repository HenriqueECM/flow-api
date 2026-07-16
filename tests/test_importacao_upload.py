"""Upload da planilha da B3: preview, revalidação e o caminho até o banco.

Estes testes sobem um .xlsx de verdade pelo endpoint, então exercitam a cadeia
completa que nenhum teste de motor alcança:

    requisição multipart -> UploadFile -> run_in_threadpool -> pandas/openpyxl
    -> parser -> classificação -> contrato de resposta

O `run_in_threadpool` só é exercitado assim. Chamar `parse_movimentacao_ativos`
direto (como fazem os testes de motor, corretamente) pula o salto do event loop
para o threadpool — e é justamente ali que um parser que bloqueie o loop, ou um
erro de serialização entre as duas pontas, apareceria.

A resposta vem em camelCase: `ReviewRow` usa `alias_generator=to_camel` e o
FastAPI serializa com `by_alias=True`. É o contrato que o frontend consome, e é
nele que as asserções batem.
"""

from decimal import Decimal
from io import BytesIO
from uuid import UUID

import pandas as pd
from sqlalchemy import func, select

from app.models import Carteira, Transacao

OUTRO_USER_ID = UUID("00000000-0000-0000-0000-0000000000bb")

# Colunas do export "Movimentação" da B3. `Instituição` não é usada pelo parser,
# mas vem no arquivo real — mantê-la prova que colunas extras não atrapalham.
COLUNAS = [
    "Entrada/Saída",
    "Data",
    "Movimentação",
    "Produto",
    "Instituição",
    "Quantidade",
    "Preço unitário",
    "Valor da Operação",
]

LIQUIDACAO = "Transferência - Liquidação"


def _linha(entrada_saida, data, mov, produto, qtd, preco, valor):
    return {
        "Entrada/Saída": entrada_saida,
        "Data": data,
        "Movimentação": mov,
        "Produto": produto,
        "Instituição": "CORRETORA XP",
        "Quantidade": qtd,
        "Preço unitário": preco,
        "Valor da Operação": valor,
    }


def _planilha(linhas) -> bytes:
    """Gera um .xlsx em memória, como o que a B3 exporta."""
    buffer = BytesIO()
    pd.DataFrame(linhas, columns=COLUNAS).to_excel(buffer, index=False)
    return buffer.getvalue()


def _upload(conteudo: bytes, nome="movimentacao.xlsx"):
    return {
        "file": (
            nome,
            conteudo,
            "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        )
    }


async def _carteira(db_session, user_id, nome="Carteira"):
    carteira = Carteira(user_id=user_id, nome=nome)
    db_session.add(carteira)
    await db_session.commit()
    return carteira.id


async def test_preview_classifica_a_planilha_e_nao_persiste(
    client, usuario_autenticado, db_session, override_get_db
):
    conteudo = _planilha(
        [
            _linha(
                "Credito",
                "05/01/2024",
                LIQUIDACAO,
                "PETR4 - PETROLEO BRASILEIRO SA",
                100,
                35.0,
                3500.0,
            )
        ]
    )

    resposta = await client.post(
        f"/carteiras/{await _carteira(db_session, usuario_autenticado.id)}"
        "/import/ativos/preview",
        files=_upload(conteudo),
    )

    assert resposta.status_code == 200, resposta.text
    corpo = resposta.json()

    assert corpo["summary"] == {
        "total": 1,
        "validas": 1,
        "alertas": 0,
        "erros": 0,
        "ignoradas": 0,
    }

    linha = corpo["rows"][0]
    assert linha["status"] == "valido"
    # O ticker é extraído do campo Produto ("PETR4 - PETROLEO ..." -> "PETR4").
    assert linha["ativo"] == "PETR4"
    assert linha["qtde"] == 100
    assert linha["precoMedio"] == 35.0
    # "Credito" vira Compra; "Debito" viraria Venda.
    assert linha["tipo"] == "Compra"
    # A B3 escreve dd/mm/aaaa; o contrato devolve ISO.
    assert linha["data"] == "2024-01-05"
    assert linha["motivo"] is None

    # Preview é revisão: o usuário ainda vai corrigir e confirmar.
    assert await db_session.scalar(select(func.count()).select_from(Transacao)) == 0


async def test_preview_reverte_a_ordem_cronologica_do_export(
    client, usuario_autenticado, db_session, override_get_db
):
    # A B3 exporta do mais recente para o mais antigo. Sem a reversão, a venda
    # seria avaliada antes da compra e viraria erro por exceder a posição — é o
    # que este teste detecta.
    conteudo = _planilha(
        [
            _linha(
                "Debito",
                "05/02/2024",
                LIQUIDACAO,
                "PETR4 - PETROLEO",
                100,
                40.0,
                4000.0,
            ),
            _linha(
                "Credito",
                "05/01/2024",
                LIQUIDACAO,
                "PETR4 - PETROLEO",
                100,
                35.0,
                3500.0,
            ),
        ]
    )

    resposta = await client.post(
        f"/carteiras/{await _carteira(db_session, usuario_autenticado.id)}"
        "/import/ativos/preview",
        files=_upload(conteudo),
    )

    assert resposta.status_code == 200, resposta.text
    corpo = resposta.json()

    assert corpo["summary"]["validas"] == 2
    assert corpo["summary"]["erros"] == 0
    # A ordem devolvida é a cronológica, não a do arquivo.
    assert [linha["data"] for linha in corpo["rows"]] == ["2024-01-05", "2024-02-05"]
    assert [linha["tipo"] for linha in corpo["rows"]] == ["Compra", "Venda"]


async def test_preview_separa_validas_ignoradas_e_erros_no_resumo(
    client, usuario_autenticado, db_session, override_get_db
):
    conteudo = _planilha(
        [
            _linha(
                "Credito",
                "05/01/2024",
                LIQUIDACAO,
                "PETR4 - PETROLEO",
                100,
                35.0,
                3500.0,
            ),
            # FII ainda não é suportado: ignorada, sem afetar a posição.
            _linha(
                "Credito",
                "06/01/2024",
                LIQUIDACAO,
                "HGLG11 - CSHG LOG FII",
                10,
                160.0,
                1600.0,
            ),
            # Não é liquidação: ignorada.
            _linha(
                "Credito", "07/01/2024", "Dividendo", "PETR4 - PETROLEO", 100, 0.5, 50.0
            ),
            # Vende 500 tendo 100: erro só nesta linha.
            _linha(
                "Debito",
                "08/01/2024",
                LIQUIDACAO,
                "PETR4 - PETROLEO",
                500,
                40.0,
                20000.0,
            ),
        ]
    )

    resposta = await client.post(
        f"/carteiras/{await _carteira(db_session, usuario_autenticado.id)}"
        "/import/ativos/preview",
        files=_upload(conteudo),
    )

    assert resposta.status_code == 200, resposta.text
    corpo = resposta.json()

    assert corpo["summary"] == {
        "total": 4,
        "validas": 1,
        "alertas": 0,
        "erros": 1,
        "ignoradas": 2,
    }
    # Toda linha não-válida precisa dizer por quê: é o que o usuário lê na tela
    # de revisão para decidir se corrige ou descarta.
    for linha in corpo["rows"]:
        if linha["status"] != "valido":
            assert linha["motivo"]


async def test_revalidate_reclassifica_sem_reenviar_o_arquivo(
    client, usuario_autenticado, db_session, override_get_db
):
    carteira_id = await _carteira(db_session, usuario_autenticado.id)
    conteudo = _planilha(
        [
            _linha(
                "Credito",
                "05/01/2024",
                LIQUIDACAO,
                "PETR4 - PETROLEO",
                100,
                35.0,
                3500.0,
            ),
            _linha(
                "Debito",
                "05/02/2024",
                LIQUIDACAO,
                "PETR4 - PETROLEO",
                500,
                40.0,
                20000.0,
            ),
        ]
    )

    preview = await client.post(
        f"/carteiras/{carteira_id}/import/ativos/preview", files=_upload(conteudo)
    )
    assert preview.status_code == 200, preview.text
    rows = preview.json()["rows"]
    assert preview.json()["summary"]["erros"] == 1

    # O usuário corrige a venda para uma quantidade que cabe na posição.
    corrigidas = [dict(linha) for linha in rows]
    corrigidas[1]["qtde"] = 40

    revalidado = await client.post(
        f"/carteiras/{carteira_id}/import/ativos/revalidate", json={"rows": corrigidas}
    )

    assert revalidado.status_code == 200, revalidado.text
    resumo = revalidado.json()["summary"]
    # A correção reabilita a linha — a posição é recalculada do zero, em ordem.
    assert resumo == {
        "total": 2,
        "validas": 2,
        "alertas": 0,
        "erros": 0,
        "ignoradas": 0,
    }


async def test_fluxo_completo_do_upload_ate_o_banco(
    client, usuario_autenticado, db_session, override_get_db
):
    """A cadeia inteira: upload -> parser -> classificação -> confirm -> Postgres.

    Nenhum outro teste liga as duas pontas. O preview devolve o contrato que o
    frontend edita, e é esse mesmo contrato que volta no confirm — se as duas
    metades divergirem (um campo renomeado, um alias trocado), é aqui que quebra.
    """
    carteira_id = await _carteira(db_session, usuario_autenticado.id)
    conteudo = _planilha(
        [
            _linha(
                "Credito",
                "05/01/2024",
                LIQUIDACAO,
                "PETR4 - PETROLEO",
                100,
                35.0,
                3500.0,
            ),
            _linha(
                "Credito", "10/01/2024", LIQUIDACAO, "VALE3 - VALE ON", 50, 60.0, 3000.0
            ),
            # Ignorada: não deve chegar ao banco.
            _linha(
                "Credito", "11/01/2024", "Dividendo", "PETR4 - PETROLEO", 100, 0.5, 50.0
            ),
        ]
    )

    preview = await client.post(
        f"/carteiras/{carteira_id}/import/ativos/preview", files=_upload(conteudo)
    )
    assert preview.status_code == 200, preview.text

    # O frontend confirma só o que está válido — as ignoradas ficam de fora.
    validas = [linha for linha in preview.json()["rows"] if linha["status"] == "valido"]
    assert len(validas) == 2

    confirm = await client.post(
        f"/carteiras/{carteira_id}/import/ativos/confirm", json={"rows": validas}
    )

    assert confirm.status_code == 200, confirm.text
    assert confirm.json() == {"criadas": 2, "falhas": []}

    linhas = (
        await db_session.execute(
            select(
                Transacao.ticker,
                Transacao.operacao,
                Transacao.quantidade,
                Transacao.preco_unit,
                Transacao.fonte,
                Transacao.carteira_id,
            ).order_by(Transacao.data)
        )
    ).all()

    assert [tx.ticker for tx in linhas] == ["PETR4", "VALE3"]
    assert linhas[0].operacao == "compra"
    assert linhas[0].quantidade == Decimal("100")
    assert linhas[0].preco_unit == Decimal("35")
    assert all(tx.fonte == "Importação B3" for tx in linhas)
    assert all(tx.carteira_id == carteira_id for tx in linhas)


async def test_preview_de_carteira_alheia_nao_processa_o_arquivo(
    client, usuario_autenticado, db_session, override_get_db
):
    assert OUTRO_USER_ID != usuario_autenticado.id
    alheia_id = await _carteira(db_session, OUTRO_USER_ID, nome="Carteira alheia")

    # Prova que a carteira existe: sem isto, o 404 seria indistinguível de
    # "não existe" e nada de isolamento estaria provado.
    assert (
        await db_session.scalar(
            select(func.count()).select_from(Carteira).where(Carteira.id == alheia_id)
        )
        == 1
    )

    conteudo = _planilha(
        [
            _linha(
                "Credito",
                "05/01/2024",
                LIQUIDACAO,
                "PETR4 - PETROLEO",
                100,
                35.0,
                3500.0,
            )
        ]
    )

    resposta = await client.post(
        f"/carteiras/{alheia_id}/import/ativos/preview", files=_upload(conteudo)
    )

    # A posse é verificada antes de o arquivo ser lido.
    assert resposta.status_code == 404, resposta.text
    assert "PETR4" not in resposta.text
