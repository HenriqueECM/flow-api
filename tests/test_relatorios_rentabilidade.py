"""Endpoint de rentabilidade: a fiação entre banco, fontes externas e motor.

O motor tem cobertura unitária em test_rentabilidade_engine.py. Aqui se testa a
costura do router: ler transações/proventos da carteira, buscar histórico (brapi),
CDI (BACEN) e IBOV (Yahoo), montar a resposta — e a tolerância a falha de fonte.

Datas derivam de `date.today()`: o motor monta a janela do 1º mês com transação
até o mês corrente, então datas fixas sairiam da janela com o tempo.
"""

import calendar
from datetime import date
from decimal import Decimal
from uuid import uuid4

import httpx

from app.models import Carteira, Transacao

HOJE = date.today()
MES_ATUAL = (HOJE.year, HOJE.month)
MES_ANT = (HOJE.year - 1, 12) if HOJE.month == 1 else (HOJE.year, HOJE.month - 1)

BRAPI_REGEX = r".*/quote/PETR4.*"
BACEN_REGEX = r".*bcdata\.sgs\.4391.*"
YAHOO_REGEX = r".*/chart/.*BVSP.*"


def _ym(par: tuple[int, int]) -> str:
    return f"{par[0]}-{par[1]:02d}"


def _epoch(par: tuple[int, int]) -> int:
    return calendar.timegm((par[0], par[1], 1, 0, 0, 0, 0, 0, 0))


async def _carteira(db_session, user_id, nome="Carteira"):
    carteira = Carteira(user_id=user_id, nome=nome)
    db_session.add(carteira)
    await db_session.commit()
    return carteira.id


def _tx_compra_mes_anterior(carteira_id):
    # Compra no 1º dia do mês anterior: fica investida o mês inteiro.
    return Transacao(
        carteira_id=carteira_id,
        ticker="PETR4",
        operacao="compra",
        quantidade=Decimal("100"),
        preco_unit=Decimal("10"),
        data=date(MES_ANT[0], MES_ANT[1], 1),
    )


def _mock_brapi(router, closes: dict[tuple[int, int], float]):
    router.get(url__regex=BRAPI_REGEX).mock(
        return_value=httpx.Response(
            200,
            json={
                "results": [
                    {
                        "symbol": "PETR4",
                        "historicalDataPrice": [
                            {"date": _epoch(par), "close": c}
                            for par, c in closes.items()
                        ],
                    }
                ]
            },
        )
    )


def _mock_bacen(router):
    router.get(url__regex=BACEN_REGEX).mock(
        return_value=httpx.Response(
            200,
            json=[
                {"data": f"01/{MES_ANT[1]:02d}/{MES_ANT[0]}", "valor": "1.00"},
                {"data": f"01/{MES_ATUAL[1]:02d}/{MES_ATUAL[0]}", "valor": "1.00"},
            ],
        )
    )


def _mock_yahoo(router):
    router.get(url__regex=YAHOO_REGEX).mock(
        return_value=httpx.Response(
            200,
            json={
                "chart": {
                    "result": [
                        {
                            "timestamp": [_epoch(MES_ANT), _epoch(MES_ATUAL)],
                            "indicators": {"quote": [{"close": [100000.0, 105000.0]}]},
                        }
                    ]
                }
            },
        )
    )


async def test_calcula_carteira_e_benchmarks(
    client, usuario_autenticado, db_session, override_get_db, bloquear_http_externo
):
    carteira_id = await _carteira(db_session, usuario_autenticado.id)
    db_session.add(_tx_compra_mes_anterior(carteira_id))
    await db_session.commit()

    # Preço sobe de 10 → 11 do mês anterior para o atual (+10%).
    _mock_brapi(bloquear_http_externo, {MES_ANT: 10.0, MES_ATUAL: 11.0})
    _mock_bacen(bloquear_http_externo)
    _mock_yahoo(bloquear_http_externo)

    resp = await client.get(f"/carteiras/{carteira_id}/relatorios/rentabilidade")

    assert resp.status_code == 200, resp.text
    corpo = resp.json()
    meses = {m["mes"]: m for m in corpo["meses"]}

    # Mês anterior: comprou e fechou a 10 → 0%. Mês atual: 1000 → 1100 → +10%.
    assert meses[_ym(MES_ANT)]["carteira"] == 0.0
    assert meses[_ym(MES_ATUAL)]["carteira"] == 10.0
    # Benchmarks reais chegaram: CDI direto, IBOV derivado dos 2 fechamentos.
    assert meses[_ym(MES_ATUAL)]["cdi"] == 1.0
    assert meses[_ym(MES_ATUAL)]["ibov"] == 5.0
    # Cards: último mês e acumulado.
    assert corpo["cards"]["mes"] == 10.0
    assert corpo["cards"]["total"] == 10.0


async def test_benchmark_indisponivel_nao_derruba_a_carteira(
    client, usuario_autenticado, db_session, override_get_db, bloquear_http_externo
):
    carteira_id = await _carteira(db_session, usuario_autenticado.id)
    db_session.add(_tx_compra_mes_anterior(carteira_id))
    await db_session.commit()

    # brapi OK; BACEN e Yahoo fora do ar.
    _mock_brapi(bloquear_http_externo, {MES_ANT: 10.0, MES_ATUAL: 11.0})
    bloquear_http_externo.get(url__regex=BACEN_REGEX).mock(
        return_value=httpx.Response(500)
    )
    bloquear_http_externo.get(url__regex=YAHOO_REGEX).mock(
        return_value=httpx.Response(500)
    )

    resp = await client.get(f"/carteiras/{carteira_id}/relatorios/rentabilidade")

    assert resp.status_code == 200, resp.text
    meses = {m["mes"]: m for m in resp.json()["meses"]}
    # A carteira continua calculada; só os benchmarks somem (None).
    assert meses[_ym(MES_ATUAL)]["carteira"] == 10.0
    assert meses[_ym(MES_ATUAL)]["cdi"] is None
    assert meses[_ym(MES_ATUAL)]["ibov"] is None


async def test_carteira_sem_transacoes_devolve_vazio(
    client, usuario_autenticado, db_session, override_get_db
):
    # Sem transação: nem chega a bater nas fontes externas (nenhuma rota mockada).
    carteira_id = await _carteira(db_session, usuario_autenticado.id)

    resp = await client.get(f"/carteiras/{carteira_id}/relatorios/rentabilidade")

    assert resp.status_code == 200, resp.text
    corpo = resp.json()
    assert corpo["meses"] == []
    assert corpo["tabela"] == []
    assert corpo["cards"]["total"] is None


async def test_carteira_inexistente_responde_404(
    client, usuario_autenticado, db_session, override_get_db
):
    resp = await client.get(f"/carteiras/{uuid4()}/relatorios/rentabilidade")

    assert resp.status_code == 404, resp.text


async def test_filtro_por_tipo_de_ativo_recorta_a_carteira(
    client, usuario_autenticado, db_session, override_get_db, bloquear_http_externo
):
    carteira_id = await _carteira(db_session, usuario_autenticado.id)
    # Uma única transação, classificada como "Ações".
    tx = _tx_compra_mes_anterior(carteira_id)
    tx.tipo_ativo = "Ações"
    db_session.add(tx)
    await db_session.commit()

    # Filtrar por um tipo que não casa → nenhuma transação → relatório vazio.
    # (Nenhuma fonte externa é consultada, então não há mocks a declarar.)
    resp = await client.get(
        f"/carteiras/{carteira_id}/relatorios/rentabilidade",
        params={"tipo_ativo": "FII"},
    )

    assert resp.status_code == 200, resp.text
    assert resp.json()["meses"] == []
