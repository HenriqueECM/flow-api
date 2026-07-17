from functools import lru_cache
from uuid import UUID

import jwt
from fastapi import Depends, HTTPException, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from jwt import PyJWKClient
from pydantic import BaseModel

from app.core.config import settings

bearer_scheme = HTTPBearer(auto_error=True)


class CurrentUser(BaseModel):
    id: UUID
    email: str | None = None


@lru_cache(maxsize=1)
def _jwks_client() -> PyJWKClient:
    """Cliente JWKS cacheado (busca/reaproveita as chaves públicas do Supabase)."""
    return PyJWKClient(settings.supabase_jwks_url)


def _decode_token(token: str) -> dict:
    """Valida o token: primeiro via JWKS (ES256), depois fallback HS256 (legado)."""
    # Principal — chaves assimétricas (ES256) do Supabase.
    try:
        signing_key = _jwks_client().get_signing_key_from_jwt(token)
        return jwt.decode(
            token,
            signing_key.key,
            algorithms=["ES256"],
            audience=settings.jwt_audience,
        )
    except jwt.PyJWTError:
        # Cai no fallback (ex.: token assinado em HS256 — sessão antiga).
        pass

    # Fallback — segredo HS256 (só se configurado).
    if settings.supabase_jwt_secret:
        return jwt.decode(
            token,
            settings.supabase_jwt_secret,
            algorithms=["HS256"],
            audience=settings.jwt_audience,
        )

    raise jwt.InvalidTokenError("Não foi possível validar o token.")


def get_current_user(
    credentials: HTTPAuthorizationCredentials = Depends(bearer_scheme),
) -> CurrentUser:
    """Valida o access token (JWT) do Supabase e devolve o usuário autenticado."""
    try:
        payload = _decode_token(credentials.credentials)
    except jwt.PyJWTError:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Token inválido ou expirado.",
        )

    sub = payload.get("sub")
    if not sub:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Token sem identificação de usuário.",
        )

    # `sub` chega como string (o PyJWT recusa outros tipos com InvalidSubjectError,
    # tratado acima), mas nada garante que seja um UUID. O Supabase sempre emite
    # um, e outro emissor — ou uma migração de provedor — não necessariamente.
    # Sem este tratamento, o UUID() estoura fora do try e o request vira 500:
    # token malformado deve ser recusado, não derrubar a rota.
    try:
        user_id = UUID(sub)
    except ValueError:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Token com identificação de usuário inválida.",
        )

    return CurrentUser(id=user_id, email=payload.get("email"))
