"""Autenticação nas rotas protegidas.

Os testes de módulo sobrescrevem `get_current_user`, então nenhum deles prova
que as rotas exigem autenticação de fato — todos passariam contra uma API
completamente aberta. Aqui o override não é usado: as requisições passam pelo
`Depends(get_current_user)` real.

Dois códigos diferentes, e a distinção é do FastAPI, não nossa: o `HTTPBearer`
responde **403** quando o header falta ou vem malformado (nem chega a nosso
código), e o nosso `get_current_user` responde **401** quando o token existe mas
não valida.

O último teste é o mais valioso do arquivo: um token ES256 de verdade,
atravessando a validação real até o banco. É o único ponto da suíte onde
`_decode_token` e o endpoint se encontram.
"""

from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from uuid import UUID, uuid4

import jwt
import pytest
from cryptography.hazmat.primitives.asymmetric import ec

from app.core import security
from app.core.config import settings
from app.models import Carteira

CID = uuid4()
SUB = "3f1b7c9e-0000-4a00-9000-abcdef123456"
AUDIENCE = "authenticated"

# Uma linha por rota protegida. A cobertura importa mais que a profundidade:
# o risco real é alguém adicionar um endpoint e esquecer a dependência.
ROTAS_PROTEGIDAS = [
    ("GET", "/carteiras"),
    ("POST", "/carteiras"),
    ("GET", "/carteiras/ativa"),
    ("GET", f"/carteiras/{CID}"),
    ("DELETE", f"/carteiras/{CID}"),
    ("GET", f"/carteiras/{CID}/transacoes"),
    ("POST", f"/carteiras/{CID}/transacoes"),
    ("GET", f"/carteiras/{CID}/proventos"),
    ("POST", f"/carteiras/{CID}/proventos"),
    ("GET", f"/carteiras/{CID}/proventos/preview"),
    ("GET", f"/carteiras/{CID}/posicoes"),
    ("GET", f"/carteiras/{CID}/relatorios/yoc"),
    ("POST", f"/carteiras/{CID}/import/ativos/revalidate"),
    ("POST", f"/carteiras/{CID}/import/ativos/confirm"),
]
IDS = [f"{metodo} {rota}" for metodo, rota in ROTAS_PROTEGIDAS]


@pytest.fixture(autouse=True)
def config_previsivel(monkeypatch):
    # O .env de desenvolvimento define SUPABASE_JWT_SECRET e o CI não; fixar o
    # valor evita que o mesmo teste tome caminhos diferentes nos dois lugares.
    monkeypatch.setattr(settings, "jwt_audience", AUDIENCE)
    monkeypatch.setattr(settings, "supabase_jwt_secret", None)


@pytest.fixture
def jwks_indisponivel(monkeypatch):
    """JWKS fora do ar. Também mantém os testes offline: o PyJWKClient usa
    urllib, que a fixture `bloquear_http_externo` não intercepta."""

    def _lanca(_token):
        raise jwt.PyJWKClientError("jwks indisponivel")

    monkeypatch.setattr(
        security,
        "_jwks_client",
        lambda: SimpleNamespace(get_signing_key_from_jwt=_lanca),
    )


@pytest.fixture
def jwks_com_a_chave(monkeypatch):
    def _instalar(publica):
        monkeypatch.setattr(
            security,
            "_jwks_client",
            lambda: SimpleNamespace(
                get_signing_key_from_jwt=lambda _t: SimpleNamespace(key=publica)
            ),
        )

    return _instalar


@pytest.mark.parametrize("metodo,rota", ROTAS_PROTEGIDAS, ids=IDS)
async def test_sem_token_a_rota_recusa(client, metodo, rota):
    resposta = await client.request(metodo, rota, json={})

    # 403 do HTTPBearer: a requisição nem chega ao get_current_user.
    assert resposta.status_code == 403, resposta.text
    assert resposta.json() == {"detail": "Not authenticated"}


@pytest.mark.parametrize("metodo,rota", ROTAS_PROTEGIDAS, ids=IDS)
async def test_token_invalido_a_rota_recusa(client, jwks_indisponivel, metodo, rota):
    resposta = await client.request(
        metodo, rota, json={}, headers={"Authorization": "Bearer token.invalido.aqui"}
    )

    # 401 nosso: o token existe, mas não valida.
    assert resposta.status_code == 401, resposta.text
    assert resposta.json() == {"detail": "Token inválido ou expirado."}


@pytest.mark.parametrize(
    "header",
    [
        "Basic dXNlcjpzZW5oYQ==",  # esquema errado
        "Bearer",  # sem credencial
        "token-solto-sem-esquema",
    ],
)
async def test_header_malformado_e_recusado(client, jwks_indisponivel, header):
    resposta = await client.get("/carteiras", headers={"Authorization": header})

    assert resposta.status_code == 403, resposta.text


async def test_rota_publica_nao_exige_token(client, override_get_db):
    # Contraste: /health é a única sem autenticação, e continua assim.
    resposta = await client.get("/health")

    assert resposta.status_code == 200, resposta.text


async def test_token_valido_autentica_de_ponta_a_ponta(
    client, jwks_com_a_chave, db_session, override_get_db
):
    """O único teste da suíte que exercita a validação real do token.

    Todos os outros sobrescrevem `get_current_user`. Este assina um JWT ES256
    com uma chave local, publica a pública no JWKS e deixa a requisição
    atravessar `_decode_token` até o Postgres — provando que o `sub` do token
    vira o `user_id` que filtra os dados.
    """
    chave = ec.generate_private_key(ec.SECP256R1())
    jwks_com_a_chave(chave.public_key())

    agora = datetime.now(tz=timezone.utc)
    token = jwt.encode(
        {
            "sub": SUB,
            "aud": AUDIENCE,
            "iat": agora,
            "exp": agora + timedelta(hours=1),
            "email": "usuario@flow.local",
        },
        chave,
        algorithm="ES256",
    )

    # Uma carteira do dono do token e outra de terceiro: o token precisa
    # selecionar só a primeira.
    db_session.add_all(
        [
            Carteira(user_id=UUID(SUB), nome="Minha"),
            Carteira(user_id=uuid4(), nome="Carteira alheia"),
        ]
    )
    await db_session.commit()

    resposta = await client.get(
        "/carteiras", headers={"Authorization": f"Bearer {token}"}
    )

    assert resposta.status_code == 200, resposta.text
    corpo = resposta.json()
    assert [c["nome"] for c in corpo] == ["Minha"]
    assert "Carteira alheia" not in resposta.text
