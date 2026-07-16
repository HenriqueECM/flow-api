"""Posições consolidadas da carteira.

Não existe entidade `Posicao`: o endpoint **deriva** a posição das transações a
cada request (via `calcular_posicao_em_data`) e a enriquece com a cotação da
brapi. Não há o que criar, persistir ou remover — o que se testa é o cálculo, o
enriquecimento e o que acontece quando a cotação não vem.

O isolamento é herdado do `get_owned_carteira` (já provado em
test_carteiras_leitura.py). O que os testes daqui acrescentam é que **este**
endpoint de fato passa pela barreira: um router novo que esquecesse o Depends
exporia as posições de todo mundo, e nada em carteiras acusaria.

`Decimal` é serializado como string no JSON, então as asserções comparam
`Decimal(corpo[...])` — assim a escala (`"200"` vs `"200.00000000"`) não
transforma uma mudança de tipo do banco em falha de teste.
"""

from datetime import date
from decimal import Decimal
from uuid import UUID, uuid4

import httpx
from sqlalchemy import func, select

from app.models import Carteira, Transacao

OUTRO_USER_ID = UUID("00000000-0000-0000-0000-0000000000bb")


def _tx(carteira_id, ticker, operacao, qtd, preco, dia, custos="0"):
    return Transacao(
        carteira_id=carteira_id,
        ticker=ticker,
        operacao=operacao,
        quantidade=Decimal(str(qtd)),
        preco_unit=Decimal(str(preco)),
        outros_custos=Decimal(str(custos)),
        data=dia,
    )


def _cotacao(symbol="PETR4", preco=30.5, variacao=1.25, nome="Petrobras PN"):
    return {
        "symbol": symbol,
        "regularMarketPrice": preco,
        "regularMarketChangePercent": variacao,
        "shortName": nome,
    }


async def _carteira(db_session, user_id, nome="Carteira"):
    carteira = Carteira(user_id=user_id, nome=nome)
    db_session.add(carteira)
    await db_session.commit()
    return carteira.id


async def test_carteira_sem_transacoes_devolve_lista_vazia(
    client, usuario_autenticado, db_session, override_get_db, bloquear_http_externo
):
    carteira_id = await _carteira(db_session, usuario_autenticado.id)

    resposta = await client.get(f"/carteiras/{carteira_id}/posicoes")

    assert resposta.status_code == 200, resposta.text
    assert resposta.json() == []
    # Sem posição aberta, a brapi não deve ser consultada. Nenhuma rota foi
    # declarada, então uma chamada teria estourado — mas o call_count torna a
    # intenção explícita em vez de acidental.
    assert bloquear_http_externo.calls.call_count == 0


async def test_consolida_compras_e_enriquece_com_a_cotacao(
    client, usuario_autenticado, db_session, override_get_db, bloquear_http_externo
):
    carteira_id = await _carteira(db_session, usuario_autenticado.id)
    db_session.add_all(
        [
            _tx(carteira_id, "PETR4", "compra", 100, "10.00", date(2024, 1, 10)),
            _tx(carteira_id, "PETR4", "compra", 100, "20.00", date(2024, 2, 10)),
        ]
    )
    await db_session.commit()

    bloquear_http_externo.get("https://brapi.dev/api/quote/PETR4").mock(
        return_value=httpx.Response(200, json={"results": [_cotacao()]})
    )

    resposta = await client.get(f"/carteiras/{carteira_id}/posicoes")

    assert resposta.status_code == 200, resposta.text
    corpo = resposta.json()
    assert len(corpo) == 1
    posicao = corpo[0]

    assert posicao["ticker"] == "PETR4"
    assert posicao["nome"] == "Petrobras PN"
    assert Decimal(posicao["quantidade"]) == Decimal("200")
    # 100x10 + 100x20 = 3000 de custo / 200 ações.
    assert Decimal(posicao["pm_historico"]) == Decimal("15")
    assert Decimal(posicao["preco_atual"]) == Decimal("30.50")
    assert posicao["variacao_percent"] == 1.25
    assert Decimal(posicao["valor_total"]) == Decimal("6100.00")
    # 200 x (30.50 - 15.00)
    assert Decimal(posicao["lucro"]) == Decimal("3100.00")


async def test_ticker_sem_cotacao_mantem_posicao_e_anula_derivados(
    client, usuario_autenticado, db_session, override_get_db, bloquear_http_externo
):
    carteira_id = await _carteira(db_session, usuario_autenticado.id)
    db_session.add(_tx(carteira_id, "PETR4", "compra", 50, "12.00", date(2024, 1, 10)))
    await db_session.commit()

    # A brapi responde, mas sem o ticker pedido (código inválido, por exemplo).
    bloquear_http_externo.get(url__regex=r".*/quote/.*").mock(
        return_value=httpx.Response(200, json={"results": []})
    )

    resposta = await client.get(f"/carteiras/{carteira_id}/posicoes")

    assert resposta.status_code == 200, resposta.text
    posicao = resposta.json()[0]

    # Quantidade e PM vêm das transações e continuam corretos...
    assert Decimal(posicao["quantidade"]) == Decimal("50")
    assert Decimal(posicao["pm_historico"]) == Decimal("12")
    # ...e o nome cai para o próprio ticker, já que não veio shortName.
    assert posicao["nome"] == "PETR4"
    # Só o que depende da cotação fica nulo — e nulo, não zero: zero seria
    # indistinguível de um ativo que realmente vale nada.
    assert posicao["preco_atual"] is None
    assert posicao["variacao_percent"] is None
    assert posicao["valor_total"] is None
    assert posicao["lucro"] is None


async def test_brapi_fora_do_ar_nao_derruba_o_endpoint(
    client, usuario_autenticado, db_session, override_get_db, bloquear_http_externo
):
    carteira_id = await _carteira(db_session, usuario_autenticado.id)
    db_session.add(_tx(carteira_id, "PETR4", "compra", 50, "12.00", date(2024, 1, 10)))
    await db_session.commit()

    bloquear_http_externo.get(url__regex=r".*/quote/.*").mock(
        side_effect=httpx.TimeoutException("brapi indisponivel")
    )

    resposta = await client.get(f"/carteiras/{carteira_id}/posicoes")

    # O contrato é este: a carteira continua legível sem a cotação. Um 500 aqui
    # deixaria o usuário sem ver a própria posição por causa de um serviço de
    # terceiro.
    assert resposta.status_code == 200, resposta.text
    posicao = resposta.json()[0]
    assert Decimal(posicao["quantidade"]) == Decimal("50")
    assert posicao["preco_atual"] is None


async def test_posicao_zerada_some_e_nao_e_cotada(
    client, usuario_autenticado, db_session, override_get_db, bloquear_http_externo
):
    carteira_id = await _carteira(db_session, usuario_autenticado.id)
    db_session.add_all(
        [
            # Ciclo encerrado: comprou e vendeu tudo.
            _tx(carteira_id, "PETR4", "compra", 100, "10.00", date(2024, 1, 10)),
            _tx(carteira_id, "PETR4", "venda", 100, "18.00", date(2024, 3, 10)),
            # Este continua aberto.
            _tx(carteira_id, "VALE3", "compra", 30, "60.00", date(2024, 1, 20)),
        ]
    )
    await db_session.commit()

    # A rota só casa VALE3: se o endpoint tentasse cotar PETR4 junto, a
    # requisição não teria rota e o teste estouraria em vez de passar.
    rota = bloquear_http_externo.get("https://brapi.dev/api/quote/VALE3").mock(
        return_value=httpx.Response(
            200, json={"results": [_cotacao(symbol="VALE3", nome="Vale ON")]}
        )
    )

    resposta = await client.get(f"/carteiras/{carteira_id}/posicoes")

    assert resposta.status_code == 200, resposta.text
    corpo = resposta.json()
    assert [p["ticker"] for p in corpo] == ["VALE3"]
    assert rota.call_count == 1


async def test_carteira_de_outro_usuario_nao_expoe_posicoes(
    client, usuario_autenticado, db_session, override_get_db, bloquear_http_externo
):
    assert OUTRO_USER_ID != usuario_autenticado.id

    alheia_id = await _carteira(db_session, OUTRO_USER_ID, nome="Carteira alheia")
    db_session.add(_tx(alheia_id, "PETR4", "compra", 100, "10.00", date(2024, 1, 10)))
    await db_session.commit()

    # Sem isto o teste seria vazio: o 404 aconteceria igual se a carteira ou a
    # transação não existissem, e nada de isolamento estaria provado.
    transacoes = await db_session.scalar(
        select(func.count())
        .select_from(Transacao)
        .where(Transacao.carteira_id == alheia_id)
    )
    assert transacoes == 1

    resposta = await client.get(f"/carteiras/{alheia_id}/posicoes")

    assert resposta.status_code == 404, resposta.text
    assert "PETR4" not in resposta.text
    # A brapi nem chega a ser consultada: a barreira corta antes.
    assert bloquear_http_externo.calls.call_count == 0


async def test_carteira_inexistente_responde_404(
    client, usuario_autenticado, db_session, override_get_db
):
    resposta = await client.get(f"/carteiras/{uuid4()}/posicoes")

    assert resposta.status_code == 404, resposta.text
