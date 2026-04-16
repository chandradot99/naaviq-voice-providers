from dotenv import load_dotenv
from pydantic_settings import BaseSettings, SettingsConfigDict

load_dotenv()


class Settings(BaseSettings):
    model_config = SettingsConfigDict(extra="ignore")

    database_url: str
    rate_limit: str = "100/minute"

    # Provider API keys — used by sync scripts
    deepgram_api_key: str = ""
    cartesia_api_key: str = ""
    elevenlabs_api_key: str = ""
    openai_api_key: str = ""  # reserved for future use

    # AI parser key — used by sync scripts that parse docs (e.g., Cartesia models)
    anthropic_api_key: str = ""


settings = Settings()
