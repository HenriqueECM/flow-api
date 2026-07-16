"""Arquivos inválidos e erros de processamento do import.

Duas famílias distintas de falha, e o endpoint as trata de formas diferentes —
de propósito:

- **O arquivo não é legível** (vazio, corrompido, sem as colunas da B3): não há
  o que revisar, então a requisição inteira falha com 400.
- **O arquivo é legível, mas uma linha é problemática** (ticker ilegível, venda
  maior que a posição): a requisição é 200 e a linha vem marcada `erro` com o
  motivo. Derrubar o upload inteiro por uma linha ruim obrigaria o usuário a
  editar o .xlsx no Excel antes de tentar de novo.

Nenhuma das duas pode virar 500.
"""

from io import BytesIO
from uuid import UUID

import pandas as pd
import pytest
from sqlalchemy import func, select

from app.models import Carteira, Transacao

OUTRO_USER_ID = UUID("00000000-0000-0000-0000-0000000000bb")

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


def _planilha(linhas, colunas=None) -> bytes:
    buffer = BytesIO()
    pd.DataFrame(linhas, columns=colunas or COLUNAS).to_excel(buffer, index=False)
    return buffer.getvalue()


def _upload(conteudo: bytes, nome="movimentacao.xlsx"):
    return {"file": (nome, conteudo, "application/octet-stream")}


async def _carteira(db_session, user_id, nome="Carteira"):
    carteira = Carteira(user_id=user_id, nome=nome)
    db_session.add(carteira)
    await db_session.commit()
    return carteira.id


async def _nada_persistido(db_session) -> bool:
    return await db_session.scalar(select(func.count()).select_from(Transacao)) == 0


# ── Arquivo ilegível: 400 ────────────────────────────────────────────────────


@pytest.mark.parametrize(
    "nome,conteudo",
    [
        ("vazio.xlsx", b""),
        ("texto.xlsx", b"isto nao e uma planilha, e texto puro"),
        # Um CSV renomeado para .xlsx — engano comum de quem exporta errado.
        ("csv-disfarcado.xlsx", b"Produto,Quantidade\nPETR4,100\n"),
        # Cabeçalho de ZIP truncado: .xlsx é um zip, e este quebra na leitura.
        ("corrompido.xlsx", b"PK\x03\x04corrompido"),
    ],
    ids=["vazio", "texto puro", "csv disfarcado", "zip corrompido"],
)
async def test_arquivo_ilegivel_responde_400_sem_persistir(
    client, usuario_autenticado, db_session, override_get_db, nome, conteudo
):
    carteira_id = await _carteira(db_session, usuario_autenticado.id)

    resposta = await client.post(
        f"/carteiras/{carteira_id}/import/ativos/preview",
        files=_upload(conteudo, nome),
    )

    # 400, não 500: o arquivo do usuário estar errado é erro dele, não da API.
    assert resposta.status_code == 400, resposta.text
    assert "Não foi possível ler a planilha" in resposta.json()["detail"]
    assert await _nada_persistido(db_session)


async def test_colunas_obrigatorias_ausentes_respondem_400_dizendo_quais(
    client, usuario_autenticado, db_session, override_get_db
):
    # Planilha legível, mas que não é o export "Movimentação" da B3.
    sem_preco = [c for c in COLUNAS if c != "Preço unitário"]
    conteudo = _planilha([], colunas=sem_preco)

    resposta = await client.post(
        f"/carteiras/{await _carteira(db_session, usuario_autenticado.id)}"
        "/import/ativos/preview",
        files=_upload(conteudo),
    )

    assert resposta.status_code == 400, resposta.text
    detalhe = resposta.json()["detail"]
    # A mensagem precisa nomear a coluna: "arquivo inválido" sozinho deixaria o
    # usuário sem saber o que corrigir.
    assert "Colunas ausentes na planilha" in detalhe
    assert "Preço unitário" in detalhe


async def test_a_extensao_nao_e_validada_o_conteudo_sim(
    client, usuario_autenticado, db_session, override_get_db
):
    """Caracteriza o comportamento atual: o nome do arquivo é ignorado.

    O router lê os bytes e entrega ao pandas; não há checagem de extensão nem de
    content-type. Um .xlsx válido renomeado para .txt é aceito, e um .txt de
    verdade seria recusado pelo parser (acima). Validar por conteúdo é mais
    robusto que por extensão — mas o comportamento não é óbvio pelo código do
    endpoint, então fica registrado aqui.
    """
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
        f"/carteiras/{await _carteira(db_session, usuario_autenticado.id)}"
        "/import/ativos/preview",
        files=_upload(conteudo, nome="dados.txt"),
    )

    assert resposta.status_code == 200, resposta.text
    assert resposta.json()["summary"]["validas"] == 1


# ── Arquivo legível, linha problemática: 200 com erro na linha ───────────────


async def test_planilha_so_com_cabecalho_devolve_resumo_zerado(
    client, usuario_autenticado, db_session, override_get_db
):
    # Nenhuma linha não é erro — é um arquivo sem movimentações no período.
    resposta = await client.post(
        f"/carteiras/{await _carteira(db_session, usuario_autenticado.id)}"
        "/import/ativos/preview",
        files=_upload(_planilha([])),
    )

    assert resposta.status_code == 200, resposta.text
    assert resposta.json() == {
        "rows": [],
        "summary": {
            "total": 0,
            "validas": 0,
            "alertas": 0,
            "erros": 0,
            "ignoradas": 0,
        },
    }


async def test_ticker_ilegivel_vira_erro_na_linha_sem_derrubar_o_upload(
    client, usuario_autenticado, db_session, override_get_db
):
    conteudo = _planilha(
        [
            # Produto vazio: não há de onde extrair o ticker.
            _linha("Credito", "05/01/2024", LIQUIDACAO, "", 100, 35.0, 3500.0),
            _linha(
                "Credito",
                "06/01/2024",
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
    assert corpo["summary"] == {
        "total": 2,
        "validas": 1,
        "alertas": 0,
        "erros": 1,
        "ignoradas": 0,
    }

    com_erro = next(linha for linha in corpo["rows"] if linha["status"] == "erro")
    assert "Não foi possível identificar o ticker" in com_erro["motivo"]
    # A linha boa continua importável: uma ruim não invalida o lote.
    assert any(linha["status"] == "valido" for linha in corpo["rows"])


async def test_linha_sem_quantidade_passa_no_preview_mas_o_confirm_recusa(
    client, usuario_autenticado, db_session, override_get_db
):
    """Caracteriza uma inconsistência entre as duas etapas.

    Quantidade ausente vira `Decimal(0)` no classificador, que só rejeita vendas
    acima da posição — então a linha é marcada **válida** no preview. O confirm,
    que valida contra `TransacaoCreate` (`quantidade` exige `> 0`), a recusa.

    O usuário vê "válida" na revisão e recebe uma falha ao confirmar. Não há
    perda de dado nem 500, mas a promessa do preview não se cumpre. Registrado
    como dívida; se o classificador passar a marcar isto como erro, este teste
    falha e deve ser atualizado.
    """
    carteira_id = await _carteira(db_session, usuario_autenticado.id)
    conteudo = _planilha(
        [_linha("Credito", "05/01/2024", LIQUIDACAO, "PETR4 - PETROLEO", None, 35.0, 0)]
    )

    preview = await client.post(
        f"/carteiras/{carteira_id}/import/ativos/preview", files=_upload(conteudo)
    )
    assert preview.status_code == 200, preview.text
    linha = preview.json()["rows"][0]
    assert linha["status"] == "valido"
    assert linha["qtde"] is None

    confirm = await client.post(
        f"/carteiras/{carteira_id}/import/ativos/confirm", json={"rows": [linha]}
    )

    assert confirm.status_code == 200, confirm.text
    corpo = confirm.json()
    assert corpo["criadas"] == 0
    assert corpo["falhas"][0]["ativo"] == "PETR4"
    assert await _nada_persistido(db_session)


async def test_linha_com_data_ilegivel_passa_no_preview_mas_o_confirm_recusa(
    client, usuario_autenticado, db_session, override_get_db
):
    # Mesma inconsistência da quantidade: data não parseável vira None, a linha
    # sai como válida e o confirm a recusa ("Linha sem data válida").
    carteira_id = await _carteira(db_session, usuario_autenticado.id)
    conteudo = _planilha(
        [
            _linha(
                "Credito",
                "data-invalida",
                LIQUIDACAO,
                "PETR4 - PETROLEO",
                100,
                35.0,
                3500.0,
            )
        ]
    )

    preview = await client.post(
        f"/carteiras/{carteira_id}/import/ativos/preview", files=_upload(conteudo)
    )
    assert preview.status_code == 200, preview.text
    linha = preview.json()["rows"][0]
    assert linha["status"] == "valido"
    assert linha["data"] is None

    confirm = await client.post(
        f"/carteiras/{carteira_id}/import/ativos/confirm", json={"rows": [linha]}
    )

    assert confirm.status_code == 200, confirm.text
    assert confirm.json()["criadas"] == 0
    assert await _nada_persistido(db_session)


async def test_revalidate_em_carteira_alheia_e_barrado(
    client, usuario_autenticado, db_session, override_get_db
):
    assert OUTRO_USER_ID != usuario_autenticado.id
    alheia_id = await _carteira(db_session, OUTRO_USER_ID, nome="Carteira alheia")

    assert (
        await db_session.scalar(
            select(func.count()).select_from(Carteira).where(Carteira.id == alheia_id)
        )
        == 1
    )

    resposta = await client.post(
        f"/carteiras/{alheia_id}/import/ativos/revalidate",
        json={
            "rows": [
                {
                    "status": "valido",
                    "ativo": "PETR4",
                    "qtde": 100,
                    "tipo": "Compra",
                    "precoMedio": 35.0,
                    "data": "2024-01-05",
                }
            ]
        },
    )

    assert resposta.status_code == 404, resposta.text
