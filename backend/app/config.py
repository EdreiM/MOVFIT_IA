from functools import lru_cache

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    app_name: str = "Monica AI"
    app_env: str = "development"
    secret_key: str = "change-me"
    access_token_expire_minutes: int = 60
    refresh_token_expire_days: int = 7
    database_url: str = "postgresql+asyncpg://monica:monica@localhost:5432/monica"
    encryption_key: str = "dev-encryption-key-32bytes!!"
    cors_origins: str = "http://localhost:5173"
    openai_default_model: str = "gpt-4o-mini"
    # URL pública do backend, usada pra montar o link completo de arquivos enviados
    # (ex: imagem de plano) — precisa ser um endereço alcançável de fora (n8n, etc.).
    public_base_url: str = "http://localhost:8000"
    # Segundos de silêncio do cliente antes da IA responder, agregando mensagens em rajada.
    ai_reply_debounce_seconds: float = 8.0
    # Tamanho máximo (em caracteres) de cada "bolha" antes de quebrar a resposta em várias mensagens.
    ai_bubble_max_chars: int = 320
    # Pausa entre bolhas ao enviar uma resposta dividida, simulando digitação natural.
    ai_bubble_delay_seconds: float = 1.2

    @property
    def cors_origins_list(self) -> list[str]:
        return [o.strip() for o in self.cors_origins.split(",") if o.strip()]


@lru_cache
def get_settings() -> Settings:
    return Settings()
