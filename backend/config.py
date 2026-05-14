from pydantic_settings import BaseSettings


class Settings(BaseSettings):
    # Database
    DATABASE_URL: str = "sqlite:///./autoform.db"

    # JWT
    JWT_SECRET: str = "dev-secret-change-me"
    JWT_ALGORITHM: str = "HS256"
    JWT_EXPIRE_MINUTES: int = 60 * 24 * 7  # 1 week

    # Token encryption (Fernet key)
    ENCRYPTION_KEY: str = ""

    # Google OAuth
    GOOGLE_CLIENT_ID: str = ""
    GOOGLE_CLIENT_SECRET: str = ""
    GOOGLE_REDIRECT_URI: str = "http://localhost:8000/google/callback"

    # LLM APIs
    GROQ_API_KEY: str = ""
    GEMINI_API_KEY: str = ""

    # App base URL (used for OAuth redirect construction)
    APP_URL: str = "http://localhost:8000"

    model_config = {"env_file": ".env", "case_sensitive": True}


settings = Settings()
