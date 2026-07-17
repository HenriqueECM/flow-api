from pydantic import field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

_DRIVER_ASYNC = "postgresql+asyncpg://"


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    # Postgres do Supabase (driver asyncpg).
    database_url: str

    @field_validator("database_url")
    @classmethod
    def _exigir_driver_asyncpg(cls, valor: str) -> str:
        # O app é async-only: db.py cria a engine com create_async_engine, que só
        # fala asyncpg. Uma URL sem o driver (`postgresql://`, como a connection
        # string crua do Supabase) faz o SQLAlchemy cair no dialeto padrão,
        # psycopg2, e estourar `ModuleNotFoundError: No module named 'psycopg2'`
        # no import de db.py — erro obscuro, longe da causa. Falhar aqui, no
        # carregamento das Settings, com a causa dita por extenso. Sem coerção
        # automática: a configuração correta é obrigatória e documentada.
        if not valor.startswith(_DRIVER_ASYNC):
            raise ValueError(
                f"DATABASE_URL deve usar o driver asyncpg ({_DRIVER_ASYNC}...). "
                "O app é async-only e não usa psycopg2; corrija o esquema da URL "
                "(a connection string crua do Supabase vem como postgresql://)."
            )
        return valor

    # Verificação de token do Supabase.
    # Principal: chaves assimétricas (ES256) via JWKS.
    supabase_jwks_url: str
    # Fallback (legado): segredo HS256 — opcional, só p/ sessões antigas.
    supabase_jwt_secret: str | None = None
    jwt_audience: str = "authenticated"

    cors_origins: str = "http://localhost:3000"
    dev_create_tables: bool = False

    # Cotações da B3 via brapi.dev (opcional — sem token, as cotações ficam
    # indisponíveis, mas a aplicação continua no ar).
    brapi_token: str | None = None

    @property
    def cors_origins_list(self) -> list[str]:
        return [o.strip() for o in self.cors_origins.split(",") if o.strip()]


settings = Settings()  # type: ignore[call-arg]
