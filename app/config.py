"""All settings come from the .env file (copy .env.example to .env)."""
import os
from dotenv import load_dotenv

load_dotenv()


def env(key: str, default: str = "") -> str:
    return (os.getenv(key) or default).strip()


class S:
    # ---------- AI language model (any OpenAI-compatible API) ----------
    # LLM_PROVIDER=mock      -> no API key needed, rule-based replies (for testing UI only)
    # LLM_PROVIDER=openai_compatible -> real AI (OpenAI, Tencent Hunyuan, DeepSeek, Qwen ...)
    LLM_PROVIDER = env("LLM_PROVIDER", "mock")
    LLM_BASE_URL = env("LLM_BASE_URL", "https://api.openai.com/v1")
    LLM_API_KEY = env("LLM_API_KEY")
    LLM_MODEL = env("LLM_MODEL", "gpt-4o-mini")
    LLM_VISION_MODEL = env("LLM_VISION_MODEL") or env("LLM_MODEL", "gpt-4o-mini")

    # ---------- Speech to text (Cantonese) ----------
    # ASR_PROVIDER = tencent | openai | none
    ASR_PROVIDER = env("ASR_PROVIDER", "none")
    TENCENT_SECRET_ID = env("TENCENT_SECRET_ID")
    TENCENT_SECRET_KEY = env("TENCENT_SECRET_KEY")
    TENCENT_REGION = env("TENCENT_REGION", "ap-guangzhou")
    TENCENT_ASR_ENGINE = env("TENCENT_ASR_ENGINE", "16k_yue")   # 16k_yue = Cantonese
    TENCENT_ASR_ENDPOINT = env("TENCENT_ASR_ENDPOINT")          # leave empty for default
    OPENAI_ASR_BASE_URL = env("OPENAI_ASR_BASE_URL", "https://api.openai.com/v1")
    OPENAI_ASR_API_KEY = env("OPENAI_ASR_API_KEY") or env("LLM_API_KEY")
    OPENAI_ASR_MODEL = env("OPENAI_ASR_MODEL", "whisper-1")

    # ---------- Text to speech (AI replies as Cantonese voice notes) ----------
    TTS_ENABLED = env("TTS_ENABLED", "false").lower() == "true"
    TTS_VOICE = env("TTS_VOICE", "zh-HK-HiuGaaiNeural")

    # ---------- WhatsApp Cloud API (Meta) ----------
    WHATSAPP_TOKEN = env("WHATSAPP_TOKEN")
    WHATSAPP_PHONE_NUMBER_ID = env("WHATSAPP_PHONE_NUMBER_ID")
    WHATSAPP_VERIFY_TOKEN = env("WHATSAPP_VERIFY_TOKEN", "aigrandkid-verify")
    WHATSAPP_APP_SECRET = env("WHATSAPP_APP_SECRET")
    GRAPH_API_VERSION = env("GRAPH_API_VERSION", "v23.0")
    GRAPH_API_BASE = env("GRAPH_API_BASE", "https://graph.facebook.com")
    # If Mum's number is not set, the first phone that messages the bot is linked to the demo family
    AUTO_LINK_FIRST_NUMBER = env("AUTO_LINK_FIRST_NUMBER", "true").lower() == "true"

    # ---------- Demo family ----------
    # Mum's WhatsApp number in international format WITHOUT "+", e.g. 85291234567
    # Leave empty to use the built-in WhatsApp simulator (http://localhost:8000/sim)
    DEMO_PARENT_WA = env("DEMO_PARENT_WA").lstrip("+").replace(" ", "")
    PARENT_NAME = env("PARENT_NAME", "Mum (黃太)")
    PARENT_NICKNAME = env("PARENT_NICKNAME", "婆婆")
    CHILD_NAME = env("CHILD_NAME", "Ka Yan")
    CHILD_NAME_ZH = env("CHILD_NAME_ZH", "嘉欣")
    CHILD_CITY = env("CHILD_CITY", "London")
    CHILD_CITY_ZH = env("CHILD_CITY_ZH", "倫敦")
    PARENT_TZ = env("PARENT_TZ", "Asia/Hong_Kong")
    CHILD_TZ = env("CHILD_TZ", "Europe/London")
    SNOOZE_MINUTES = int(env("SNOOZE_MINUTES", "10"))
    # Mum's waking hours: the AI only sends proactive messages inside them (HK time)
    WAKE_START = env("WAKE_START", "08:30")
    WAKE_END = env("WAKE_END", "21:30")
    MORNING_DELIVERY = env("MORNING_DELIVERY", "09:15")   # messages written at night arrive here
    BRIEF_TIME = env("BRIEF_TIME", "07:30")               # child's morning brief, child's local time
    CHILD_LANG = env("CHILD_LANG", "English")
    # Demo: confirmed plans happen immediately (clock jumps to their time) and the app resets on every start
    DEMO_INSTANT = env("DEMO_INSTANT", "true").lower() == "true"
    RESET_ON_START = env("RESET_ON_START", "true").lower() == "true"

    DB_PATH = env("DB_PATH", "data/aigrandkid.db")
    MEDIA_DIR = env("MEDIA_DIR", "data/media")
