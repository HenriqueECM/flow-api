"""Validação do token do Supabase — o último ponto cego estrutural do harness.

Todo o resto da suíte sobrescreve `get_current_user`, então até aqui a suíte
inteira passaria com `_decode_token` quebrado. Estes testes fecham isso.

As chaves são geradas localmente (ES256/P-256) e o JWKS é substituído por um
objeto em memória: nada aqui toca a rede. Isso importa em especial porque o
`PyJWKClient` usa `urllib`, não `httpx` — a fixture `bloquear_http_externo` não
o alcança, e sem o monkeypatch estes testes fariam requisições de verdade.

`settings` também é sempre controlado por monkeypatch: o `.env` de
desenvolvimento define `SUPABASE_JWT_SECRET` e o CI não, e sem fixar o valor os
testes se comportariam de formas diferentes nos dois ambientes.
"""

from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from uuid import UUID

import jwt
import pytest
from cryptography.hazmat.primitives.asymmetric import ec
from fastapi import HTTPException
from fastapi.security import HTTPAuthorizationCredentials

from app.core import security
from app.core.config import settings
from app.core.security import get_current_user

SUB = "3f1b7c9e-0000-4a00-9000-abcdef123456"
AUDIENCE = "authenticated"
SEGREDO_HS256 = "segredo-legado-de-teste"


def _credenciais(token: str) -> HTTPAuthorizationCredentials:
    return HTTPAuthorizationCredentials(scheme="Bearer", credentials=token)


def _claims(**extra):
    agora = datetime.now(tz=timezone.utc)
    return {
        "sub": SUB,
        "aud": AUDIENCE,
        "iat": agora,
        "exp": agora + timedelta(hours=1),
        "email": "usuario@flow.local",
        **extra,
    }


@pytest.fixture
def chave():
    """Par de chaves ES256 (P-256) — o mesmo algoritmo que o Supabase usa."""
    return ec.generate_private_key(ec.SECP256R1())


@pytest.fixture(autouse=True)
def config_previsivel(monkeypatch):
    monkeypatch.setattr(settings, "jwt_audience", AUDIENCE)
    monkeypatch.setattr(settings, "supabase_jwt_secret", None)


@pytest.fixture
def jwks(monkeypatch):
    """Instala um JWKS em memória que devolve a chave pública informada."""

    def _com_chave_publica(publica):
        cliente = SimpleNamespace(
            get_signing_key_from_jwt=lambda _token: SimpleNamespace(key=publica)
        )
        monkeypatch.setattr(security, "_jwks_client", lambda: cliente)

    return _com_chave_publica


@pytest.fixture
def jwks_indisponivel(monkeypatch):
    """Simula o JWKS fora do ar (rede, DNS, 5xx do Supabase)."""

    def _falhar(erro=None):
        erro = erro or jwt.PyJWKClientError("jwks indisponivel")

        def _lanca(_token):
            raise erro

        monkeypatch.setattr(
            security,
            "_jwks_client",
            lambda: SimpleNamespace(get_signing_key_from_jwt=_lanca),
        )

    return _falhar


# ── Caminho principal: ES256 via JWKS ────────────────────────────────────────


def test_token_es256_valido_devolve_o_usuario(chave, jwks):
    jwks(chave.public_key())
    token = jwt.encode(_claims(), chave, algorithm="ES256")

    usuario = get_current_user(_credenciais(token))

    assert usuario.id == UUID(SUB)
    assert usuario.email == "usuario@flow.local"


def test_token_sem_email_e_aceito(chave, jwks):
    # `email` é opcional no CurrentUser: um token sem o claim continua válido.
    jwks(chave.public_key())
    claims = _claims()
    del claims["email"]
    token = jwt.encode(claims, chave, algorithm="ES256")

    assert get_current_user(_credenciais(token)).email is None


# ── Recusas ──────────────────────────────────────────────────────────────────


def test_assinatura_de_outra_chave_e_recusada(chave, jwks):
    # O JWKS publica a chave A; o token foi assinado com a B. É o cenário de
    # token forjado — o mais importante do arquivo.
    outra = ec.generate_private_key(ec.SECP256R1())
    jwks(chave.public_key())
    token = jwt.encode(_claims(), outra, algorithm="ES256")

    with pytest.raises(HTTPException) as exc:
        get_current_user(_credenciais(token))

    assert exc.value.status_code == 401
    assert exc.value.detail == "Token inválido ou expirado."


def test_token_expirado_e_recusado(chave, jwks):
    jwks(chave.public_key())
    passado = datetime.now(tz=timezone.utc) - timedelta(hours=2)
    token = jwt.encode(
        _claims(iat=passado, exp=passado + timedelta(hours=1)), chave, algorithm="ES256"
    )

    with pytest.raises(HTTPException) as exc:
        get_current_user(_credenciais(token))

    assert exc.value.status_code == 401


def test_audience_errada_e_recusada(chave, jwks):
    # Um token legítimo emitido para OUTRO público não vale aqui.
    jwks(chave.public_key())
    token = jwt.encode(_claims(aud="outro-servico"), chave, algorithm="ES256")

    with pytest.raises(HTTPException) as exc:
        get_current_user(_credenciais(token))

    assert exc.value.status_code == 401


def test_token_sem_sub_e_recusado_com_mensagem_propria(chave, jwks):
    jwks(chave.public_key())
    claims = _claims()
    del claims["sub"]
    token = jwt.encode(claims, chave, algorithm="ES256")

    with pytest.raises(HTTPException) as exc:
        get_current_user(_credenciais(token))

    assert exc.value.status_code == 401
    assert exc.value.detail == "Token sem identificação de usuário."


def test_token_com_sub_vazio_e_recusado(chave, jwks):
    jwks(chave.public_key())
    token = jwt.encode(_claims(sub=""), chave, algorithm="ES256")

    with pytest.raises(HTTPException) as exc:
        get_current_user(_credenciais(token))

    assert exc.value.status_code == 401


def test_texto_qualquer_no_lugar_do_token_e_recusado(jwks_indisponivel):
    jwks_indisponivel()

    with pytest.raises(HTTPException) as exc:
        get_current_user(_credenciais("isto-nao-e-um-jwt"))

    assert exc.value.status_code == 401


def test_algoritmo_none_e_recusado(chave, jwks):
    # Ataque clássico: token sem assinatura declarando alg=none. O `algorithms`
    # explícito do jwt.decode é o que barra.
    jwks(chave.public_key())
    token = jwt.encode(_claims(), key="", algorithm="none")

    with pytest.raises(HTTPException) as exc:
        get_current_user(_credenciais(token))

    assert exc.value.status_code == 401


# ── Fallback HS256 (sessões legadas) ─────────────────────────────────────────


def test_hs256_e_aceito_quando_o_jwks_nao_valida(jwks_indisponivel, monkeypatch):
    monkeypatch.setattr(settings, "supabase_jwt_secret", SEGREDO_HS256)
    jwks_indisponivel()
    token = jwt.encode(_claims(), SEGREDO_HS256, algorithm="HS256")

    assert get_current_user(_credenciais(token)).id == UUID(SUB)


def test_hs256_com_segredo_errado_e_recusado(jwks_indisponivel, monkeypatch):
    monkeypatch.setattr(settings, "supabase_jwt_secret", SEGREDO_HS256)
    jwks_indisponivel()
    token = jwt.encode(_claims(), "outro-segredo", algorithm="HS256")

    with pytest.raises(HTTPException) as exc:
        get_current_user(_credenciais(token))

    assert exc.value.status_code == 401


def test_sem_jwks_e_sem_segredo_configurado_recusa(jwks_indisponivel):
    # `supabase_jwt_secret` é None (config_previsivel): sem o fallback, o
    # _decode_token levanta InvalidTokenError e vira 401 — não 500.
    jwks_indisponivel()
    token = jwt.encode(_claims(), "qualquer", algorithm="HS256")

    with pytest.raises(HTTPException) as exc:
        get_current_user(_credenciais(token))

    assert exc.value.status_code == 401


@pytest.mark.parametrize(
    "erro",
    [
        jwt.PyJWKClientConnectionError("sem rede"),
        jwt.PyJWKClientError("jwks malformado"),
    ],
)
def test_falha_de_infra_no_jwks_vira_401_e_nao_500(jwks_indisponivel, erro):
    # O Supabase fora do ar deve recusar o acesso, não derrubar a API com um
    # erro não tratado.
    jwks_indisponivel(erro)

    with pytest.raises(HTTPException) as exc:
        get_current_user(_credenciais("qualquer.coisa.aqui"))

    assert exc.value.status_code == 401


# ── Formato do `sub` ─────────────────────────────────────────────────────────


@pytest.mark.parametrize(
    "sub",
    ["nao-e-um-uuid", "user-123", "3f1b7c9e-0000-4a00-9000", "00000000000000000000"],
    ids=["texto", "texto com hifen", "uuid truncado", "digitos demais"],
)
def test_sub_que_nao_e_uuid_e_recusado_com_401(chave, jwks, sub):
    # Antes, o UUID(sub) ficava fora do try/except e um token assinado com `sub`
    # textual virava ValueError não tratado — 500 em vez de 401. Não era
    # explorável (exige assinatura válida), mas derrubava a rota.
    jwks(chave.public_key())
    token = jwt.encode(_claims(sub=sub), chave, algorithm="ES256")

    with pytest.raises(HTTPException) as exc:
        get_current_user(_credenciais(token))

    assert exc.value.status_code == 401
    assert exc.value.detail == "Token com identificação de usuário inválida."


@pytest.mark.parametrize("sub", [123, 12.5, ["uuid"], {"id": "uuid"}])
def test_sub_que_nao_e_texto_e_recusado_pelo_pyjwt(chave, jwks, sub):
    # O `sub` vem de JSON e poderia chegar como número ou objeto. Não chega ao
    # UUID(): o PyJWT valida o tipo no decode (InvalidSubjectError, subclasse de
    # PyJWTError) e o erro já cai no tratamento de token inválido. O teste trava
    # essa garantia — ela vem da versão pinada da biblioteca, não do nosso código.
    jwks(chave.public_key())
    token = jwt.encode(_claims(sub=sub), chave, algorithm="ES256")

    with pytest.raises(HTTPException) as exc:
        get_current_user(_credenciais(token))

    assert exc.value.status_code == 401
    assert exc.value.detail == "Token inválido ou expirado."
