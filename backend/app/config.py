#from pydantic_settings import BaseSettings
from pydantic_settings import BaseSettings, SettingsConfigDict
from pydantic import ConfigDict
from typing import Optional


class Settings(BaseSettings):
    model_config = ConfigDict(env_file=".env", extra="ignore")

    # Optional when DATABASE_URL is provided (Replit / Railway)
    RESEND_API_KEY: Optional[str] = None
    
    POSTGRES_USER: Optional[str] = None
    POSTGRES_PASSWORD: Optional[str] = None
    POSTGRES_DB: Optional[str] = None
    POSTGRES_HOST: str = "localhost"
    POSTGRES_PORT: int = 5432
    
    SECRET_KEY: str
    JWT_SECRET_KEY: str
    DEBUG: bool = False
    
    # Email SMTP
    SMTP_HOST:      str = ""
    SMTP_PORT:      int = 587
    SMTP_USER:      str = ""
    SMTP_PASSWORD:  str = ""
    SMTP_FROM_NAME: str = "Nyumba Zetu Sales"

    # Resend
    RESEND_API_KEY:    str = ""
    COMMS_FROM_EMAIL:  str = "onboarding@resend.dev"
    COMMS_FROM_NAME:   str = "Nyumba Zetu"
    APP_URL:           str = "https://nyumba-lead-hub.vercel.app"

    # Groq
    GROQ_API_KEY: str = ""

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
