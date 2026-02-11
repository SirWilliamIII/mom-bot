import os
from dotenv import load_dotenv

load_dotenv()


class Config:
    STT_PROVIDER = os.getenv("STT_PROVIDER", "openai").lower()
    LLM_PROVIDER = os.getenv("LLM_PROVIDER", "openai").lower()
    TTS_PROVIDER = os.getenv("TTS_PROVIDER", "openai").lower()

    OPENAI_API_KEY = os.getenv("OPENAI_API_KEY", "")
    OPENAI_LLM_MODEL = os.getenv("OPENAI_LLM_MODEL", "gpt-4o-mini")
    OPENAI_TTS_VOICE = os.getenv("OPENAI_TTS_VOICE", "nova")

    GEMINI_API_KEY = os.getenv("GEMINI_API_KEY", "")
    GEMINI_MODEL = os.getenv("GEMINI_MODEL", "gemini-2.0-flash")

    COMPANION_NAME = os.getenv("COMPANION_NAME", "Piglet")
    SYSTEM_PROMPT = os.getenv("SYSTEM_PROMPT", "")

    CHAT_HISTORY_RESET_TIME = int(os.getenv("CHAT_HISTORY_RESET_TIME", "300"))

    INITIAL_VOLUME_LEVEL = int(os.getenv("INITIAL_VOLUME_LEVEL", "114"))
    SOUND_CARD_NAME = os.getenv("SOUND_CARD_NAME", "wm8960soundcard")

    CUSTOM_FONT_PATH = os.getenv("CUSTOM_FONT_PATH", "")
    MUSIC_DIR = os.getenv("MUSIC_DIR", os.path.join(os.path.dirname(__file__), "assets", "music"))

    @classmethod
    def validate(cls):
        errors = []
        if cls.STT_PROVIDER == "openai" and not cls.OPENAI_API_KEY:
            errors.append("OPENAI_API_KEY is required when STT_PROVIDER=openai")
        if cls.LLM_PROVIDER == "openai" and not cls.OPENAI_API_KEY:
            errors.append("OPENAI_API_KEY is required when LLM_PROVIDER=openai")
        if cls.TTS_PROVIDER == "openai" and not cls.OPENAI_API_KEY:
            errors.append("OPENAI_API_KEY is required when TTS_PROVIDER=openai")
        if cls.LLM_PROVIDER == "gemini" and not cls.GEMINI_API_KEY:
            errors.append("GEMINI_API_KEY is required when LLM_PROVIDER=gemini")
        if errors:
            for e in errors:
                print(f"[Config Error] {e}")
            return False
        return True
