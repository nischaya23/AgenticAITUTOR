"""Environment configuration; launch commands run from the project root."""
import os
from pathlib import Path
from dotenv import load_dotenv

ROOT = Path(__file__).resolve().parents[1]
load_dotenv(ROOT / '.env')
COOKIE_SECURE = os.getenv('COOKIE_SECURE', 'false').lower() == 'true'
OPENAI_MODEL = os.getenv('OPENAI_MODEL', 'gpt-4.1-mini')
OPENAI_API_KEY = os.getenv('OPENAI_API_KEY', '')
DATABASE_URL = os.getenv('DATABASE_URL', f'sqlite:///{ROOT / "tutor.db"}')
DATA_DIR = Path(os.getenv('DATA_DIR', str(ROOT / 'data'))).resolve()
API_TIMEOUT = float(os.getenv('API_TIMEOUT', '30'))
MAX_OUTPUT_TOKENS = int(os.getenv('MAX_OUTPUT_TOKENS', '1600'))
MAX_INPUT_CHARS = int(os.getenv('MAX_INPUT_CHARS', '60000'))
FAILURE_THRESHOLD = int(os.getenv('FAILURE_THRESHOLD', '1'))
STALL_SECONDS = float(os.getenv('STALL_SECONDS', '30'))
IDLE_SECONDS = float(os.getenv('IDLE_SECONDS', '60'))
COOLDOWN_SECONDS = float(os.getenv('COOLDOWN_SECONDS', '45'))
AGGREGATION_SECONDS = float(os.getenv('AGGREGATION_SECONDS', '5'))
TYPING_GRACE_SECONDS = float(os.getenv('TYPING_GRACE_SECONDS', '3'))
PROVIDER_CONCURRENCY = max(1, int(os.getenv('PROVIDER_CONCURRENCY', '3')))
HYPOTHESIS_PAUSE_SECONDS = 8
HYPOTHESIS_INTERVAL_SECONDS = 30
BANDIT_WARMUP = int(os.getenv('BANDIT_WARMUP', '12'))
MAX_UPLOAD_BYTES = 8 * 1024 * 1024
MAX_UPLOAD_FILES = 10
# Web search: "free" (Wikipedia + DuckDuckGo, no key), "gemini" (Google Search grounding), or "off".
WEB_SEARCH_PROVIDER = os.getenv('WEB_SEARCH_PROVIDER', 'free').lower()
WEB_SEARCH_MAX_PAGES = int(os.getenv('WEB_SEARCH_MAX_PAGES', '4'))
WEB_SEARCH_TIMEOUT = float(os.getenv('WEB_SEARCH_TIMEOUT', '15'))
WEB_SEARCH_CONTACT = os.getenv('WEB_SEARCH_CONTACT', 'https://github.com/nischaya23/AgenticAITUTOR')
LLM_PROVIDER = os.getenv("LLM_PROVIDER", "openai").lower()
GEMINI_API_KEY = os.getenv("GEMINI_API_KEY", "")
GEMINI_MODEL = os.getenv("GEMINI_MODEL", "")
