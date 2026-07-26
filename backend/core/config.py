from functools import lru_cache
import os
from pathlib import Path

from pydantic_settings import BaseSettings, SettingsConfigDict


def _load_env_files() -> None:
    """Load crime-ai/.env and backend/.env; map nonstandard demo key names."""
    try:
        from dotenv import load_dotenv
    except ImportError:
        return
    root = Path(__file__).resolve().parents[2]  # crime-ai/
    load_dotenv(root / ".env", override=False)
    load_dotenv(Path(__file__).resolve().parents[1] / ".env", override=True)

    # Demo .env used neon_DB / NEO4J_USERNAME / SARVAM_API instead of standard names.
    aliases = {
        "DATABASE_URL": ("DATABASE_URL", "neon_DB", "NEON_DB", "POSTGRES_URL"),
        "NEO4J_USER": ("NEO4J_USER", "NEO4J_USERNAME"),
        "SARVAM_API_KEY": ("SARVAM_API_KEY", "SARVAM_API"),
        "ELEVENLABS_API_KEY": ("ELEVENLABS_API_KEY", "ELEVEN_LABS_API_KEY", "ELEVEN_API_KEY"),
    }
    for canonical, keys in aliases.items():
        if os.getenv(canonical):
            continue
        for k in keys[1:]:
            v = os.getenv(k)
            if v:
                os.environ[canonical] = v
                break


_load_env_files()


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    app_env: str = "development"
    jwt_secret: str = "dev-only-change-me"
    database_url: str = ""
    neo4j_uri: str = ""
    neo4j_user: str = ""
    neo4j_password: str = ""
    gemini_api_key: str = ""
    gemini_model: str = "gemini-2.5-flash"
    sarvam_api_key: str = ""
    sarvam_stt_model: str = "saarika:v2.5"
    sarvam_tts_model: str = "bulbul:v3"
    sarvam_tts_speaker: str = "simran"
    # ElevenLabs — preferred natural TTS for voice calls (falls back to Sarvam / browser).
    elevenlabs_api_key: str = ""
    elevenlabs_voice_id: str = "EXAVITQu4vr4xnSDxMaL"  # Sarah — clear, natural
    elevenlabs_model: str = "eleven_multilingual_v2"
    # Kannada is only supported by eleven_v3 — multilingual_v2/flash mispronounce it.
    elevenlabs_model_kn: str = "eleven_v3"
    elevenlabs_output_format: str = "mp3_44100_128"
    # Which engine speaks Kannada: "sarvam" (native Indic voice) or "elevenlabs" (eleven_v3).
    kannada_tts_provider: str = "sarvam"
    livekit_url: str = ""
    livekit_api_key: str = ""
    livekit_api_secret: str = ""
    catalyst_project_id: str = ""
    catalyst_client_id: str = ""
    catalyst_client_secret: str = ""


@lru_cache
def get_settings() -> Settings:
    return Settings()
