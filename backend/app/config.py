from pydantic_settings import BaseSettings
from pydantic import ConfigDict


class Settings(BaseSettings):
    model_config = ConfigDict(env_file=".env", extra="ignore")

    POSTGRES_USER: str
    POSTGRES_PASSWORD: str
    POSTGRES_DB: str
    POSTGRES_HOST: str = "db"
    POSTGRES_PORT: int = 5432
    SECRET_KEY: str
    JWT_SECRET_KEY: str
    DEBUG: bool = False
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
