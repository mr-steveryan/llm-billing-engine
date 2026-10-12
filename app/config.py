from pathlib import Path

from pydantic import SecretStr
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    # Real environment variables win over .env. In Docker, .env isn't in the image (.dockerignore);
    # compose's env_file injects the same values as environment variables instead.
    # Unknown keys in .env are an error (pydantic-settings default), so a typo fails at startup.
    model_config = SettingsConfigDict(env_file=Path(__file__).parent.parent / ".env", str_min_length=1)

    database_url: str
    razorpay_key_id: str
    razorpay_key_secret: SecretStr  # SecretStr: prints as ********** if a settings object is ever logged
    razorpay_webhook_secret: SecretStr
    razorpay_plan_id: str


# Loaded once at import: missing config crashes the app at startup, not on the first webhook.
settings = Settings()  # pyright: ignore[reportCallIssue] -- fields come from env/.env, not arguments
