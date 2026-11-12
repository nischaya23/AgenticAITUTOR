"""Environment configuration; launch commands run from the project root."""
import os
from pathlib import Path
from dotenv import load_dotenv

ROOT = Path(__file__).resolve().parents[1]
load_dotenv(ROOT / '.env')
COOKIE_SECURE = os.getenv('COOKIE_SECURE', 'false').lower() == 'true'
CORS_ORIGINS = tuple(origin.strip() for origin in os.getenv(
    'CORS_ORIGINS', 'http://localhost:5173,http://127.0.0.1:5173').split(',') if origin.strip())
OPENAI_MODEL = os.getenv('OPENAI_MODEL', 'gpt-4.1-mini')
OPENAI_API_KEY = os.getenv('OPENAI_API_KEY', '')
DATABASE_URL = os.getenv('DATABASE_URL', f'sqlite:///{ROOT / "tutor.db"}')
DATA_DIR = Path(os.getenv('DATA_DIR', str(ROOT / 'data'))).resolve()
API_TIMEOUT = float(os.getenv('API_TIMEOUT', '30'))
MAX_OUTPUT_TOKENS = int(os.getenv('MAX_OUTPUT_TOKENS', '1600'))
MAX_INPUT_CHARS = int(os.getenv('MAX_INPUT_CHARS', '60000'))
PROVIDER_CONCURRENCY = max(1, int(os.getenv('PROVIDER_CONCURRENCY', '3')))
# Resilience for overloaded providers (HTTP 429/5xx): rounds of attempts on top of the SDK's own quick retry,
# exponential backoff with jitter, and a shared cooldown so parallel calls back off together.
LLM_MAX_ATTEMPTS = max(1, int(os.getenv('LLM_MAX_ATTEMPTS', '4')))
LLM_BACKOFF_SECONDS = float(os.getenv('LLM_BACKOFF_SECONDS', '2'))
LLM_MAX_BACKOFF_SECONDS = float(os.getenv('LLM_MAX_BACKOFF_SECONDS', '30'))
HYPOTHESIS_PAUSE_SECONDS = 8
HYPOTHESIS_INTERVAL_SECONDS = 30
MAX_UPLOAD_BYTES = 8 * 1024 * 1024
MAX_UPLOAD_FILES = 10
# Web search: "free" (Wikipedia + DuckDuckGo, no key), "gemini" (Google Search grounding), or "off".
WEB_SEARCH_PROVIDER = os.getenv('WEB_SEARCH_PROVIDER', 'free').lower()
WEB_SEARCH_ON_UPLOAD = os.getenv('WEB_SEARCH_ON_UPLOAD', 'true').strip().lower() == 'true'
WEB_SEARCH_MAX_PAGES = int(os.getenv('WEB_SEARCH_MAX_PAGES', '4'))
WEB_SEARCH_TIMEOUT = float(os.getenv('WEB_SEARCH_TIMEOUT', '15'))
WEB_SEARCH_CONTACT = os.getenv('WEB_SEARCH_CONTACT', 'https://github.com/nischaya23/AgenticAITUTOR')
LLM_PROVIDER = os.getenv("LLM_PROVIDER", "openai").lower()
GEMINI_API_KEY = os.getenv("GEMINI_API_KEY", "")
GEMINI_MODEL = os.getenv("GEMINI_MODEL", "")
# Optional backup models used for the later attempts when the main model stays overloaded.
GEMINI_FALLBACK_MODEL = os.getenv("GEMINI_FALLBACK_MODEL", "")
OPENAI_FALLBACK_MODEL = os.getenv("OPENAI_FALLBACK_MODEL", "")
OBSERVER_LLM_ENABLED = os.getenv('OBSERVER_LLM_ENABLED', 'true').lower() == 'true'
OBSERVER_PAUSE_SECONDS = max(0, float(os.getenv('OBSERVER_PAUSE_SECONDS', '8')))
IDLE_SECONDS = max(0, float(os.getenv('IDLE_SECONDS', '60')))
OBSERVER_REVIEW_SECONDS = max(0, float(os.getenv('OBSERVER_REVIEW_SECONDS', '30')))
