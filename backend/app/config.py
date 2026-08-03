from pydantic_settings import BaseSettings
from pydantic import ConfigDict
from typing import Optional


class Settings(BaseSettings):
    model_config = ConfigDict(env_file=".env", extra="ignore")

    # Optional when DATABASE_URL is provided (Replit / Railway)
    POSTGRES_USER: Optional[str] = None
    POSTGRES_PASSWORD: Optional[str] = None
    POSTGRES_DB: Optional[str] = None
    POSTGRES_HOST: str = "localhost"
    POSTGRES_PORT: int = 5432
    SECRET_KEY: str
    JWT_SECRET_KEY: str
    DEBUG: bool = False
    APP_ENV: str = "development"
    EMAIL_DELIVERY_MODE: str = "normal"
    EMAIL_ALLOWED_RECIPIENTS: str = ""
    SMTP_HOST: str = ""
    SMTP_PORT: int = 587
    SMTP_USER: str = ""
    SMTP_PASSWORD: str = ""
    SMTP_USE_TLS: bool = True
    COMMUNICATIONS_WORKER_TOKEN: str = ""
    COMMUNICATIONS_WORKER_BATCH_SIZE: int = 25
    COMMUNICATIONS_MAX_ATTEMPTS: int = 5
    NEWSLETTER_UNSUBSCRIBE_SECRET: str = ""
    EMAIL_WEBHOOK_SECRET: str = ""
    NEWSLETTER_AI_MODEL: str = "gemini-2.5-flash"
    NEWSLETTER_AI_TIMEOUT_SECONDS: int = 45
    PUBLIC_API_BASE_URL: str = "http://localhost:8000"
    COMMUNICATIONS_LOCK_TIMEOUT_MINUTES: int = 15
    ALLOWED_ORIGINS: str = (
        "http://localhost:5173,"
        "http://127.0.0.1:8080,"
        "https://preview--nyumba-lead-hub.lovable.app,"
        "https://nyumba-lead-hub.vercel.app"
    )

    @property
    def database_url(self) -> str:
        return (
            f"postgresql://{self.POSTGRES_USER}:{self.POSTGRES_PASSWORD}"
            f"@{self.POSTGRES_HOST}:{self.POSTGRES_PORT}/{self.POSTGRES_DB}"
        )


settings = Settings()
