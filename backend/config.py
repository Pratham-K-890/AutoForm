import sys
from pydantic_settings import BaseSettings


class Settings(BaseSettings):
    DATABASE_URL: str = "sqlite:///./autoform.db"

    JWT_SECRET: str = "dev-secret-change-me"
    JWT_ALGORITHM: str = "HS256"
    JWT_EXPIRE_MINUTES: int = 60 * 24 * 7  # 1 week

    ENCRYPTION_KEY: str = ""

    GOOGLE_CLIENT_ID: str = ""
    GOOGLE_CLIENT_SECRET: str = ""
    GOOGLE_REDIRECT_URI: str = "http://localhost:8000/google/callback"

    GROQ_API_KEY: str = ""
    GEMINI_API_KEY: str = ""

    APP_URL: str = "http://localhost:8000"

    model_config = {"env_file": ".env", "case_sensitive": True}

    def validate_production(self) -> None:
        """Fail fast if critical secrets are still at their default values."""
        errors = []
        if self.JWT_SECRET == "dev-secret-change-me":
            errors.append("JWT_SECRET must be set to a secure random value")
        if not self.GROQ_API_KEY:
            errors.append("GROQ_API_KEY is required")
        if not self.GEMINI_API_KEY:
            errors.append("GEMINI_API_KEY is required")
        if errors:
            print("STARTUP ERROR — missing or insecure configuration:")
            for e in errors:
                print(f"  • {e}")
            sys.exit(1)


settings = Settings()
