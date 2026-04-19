from dotenv import load_dotenv
from pydantic_settings import BaseSettings, SettingsConfigDict

load_dotenv()


class Settings(BaseSettings):
    model_config = SettingsConfigDict(extra="ignore")

    database_url: str                      # dev / local DB
    prod_database_url: str = ""            # prod DB — only needed for scripts/promote.py
    rate_limit: str = "100/minute"

    # Provider API keys — used by sync scripts
    deepgram_api_key: str = ""
    cartesia_api_key: str = ""
    elevenlabs_api_key: str = ""
    openai_api_key: str = ""
    google_cloud_api_key: str = ""
    sarvam_api_key: str = ""
    azure_speech_key: str = ""
    azure_speech_region: str = "eastus"
    aws_access_key_id: str = ""
    aws_secret_access_key: str = ""
    aws_region: str = "us-east-1"
    hume_api_key: str = ""
    inworld_api_key: str = ""
    murf_api_key: str = ""
    speechmatics_api_key: str = ""
    lmnt_api_key: str = ""
    assemblyai_api_key: str = ""
    revai_api_key: str = ""
    gladia_api_key: str = ""
    minimax_api_key: str = ""
    ibm_tts_api_key: str = ""
    ibm_stt_api_key: str = ""
    neuphonic_api_key: str = ""
    resemble_api_key: str = ""
    fish_audio_api_key: str = ""
    unreal_speech_api_key: str = ""
    smallest_api_key: str = ""
    ibm_tts_url: str = "https://api.us-south.text-to-speech.watson.cloud.ibm.com"
    ibm_stt_url: str = "https://api.us-south.speech-to-text.watson.cloud.ibm.com"

    # AI parser key — used by sync scripts that parse docs (e.g., Cartesia models)
    anthropic_api_key: str = ""


settings = Settings()
