from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    # Postgres do Supabase (driver asyncpg).
    database_url: str

    # Verificação de token do Supabase.
    # Principal: chaves assimétricas (ES256) via JWKS.
    supabase_jwks_url: str
    # Fallback (legado): segredo HS256 — opcional, só p/ sessões antigas.
    supabase_jwt_secret: str | None = None
    jwt_audience: str = "authenticated"

    cors_origins: str = "http://localhost:3000"
    dev_create_tables: bool = False

    @property
    def cors_origins_list(self) -> list[str]:
        return [o.strip() for o in self.cors_origins.split(",") if o.strip()]


settings = Settings()  # type: ignore[call-arg]
