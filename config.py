"""Central configuration — all magic values in one place, driven by env vars."""
import os
from dotenv import load_dotenv

load_dotenv()

# authentication
TEST_USERNAME: str = os.getenv("TEST_USERNAME", "")
TEST_PASSWORD: str = os.getenv("TEST_PASSWORD", "")

# browser
BROWSER_HEADLESS: bool = os.getenv("BROWSER_HEADLESS", "true").lower() != "false"
DEFAULT_BROWSER:  str  = os.getenv("DEFAULT_BROWSER", "chromium")

# paths
SCREENSHOT_DIR: str = os.getenv("SCREENSHOT_DIR", "output/screenshots")
REPORT_DIR:     str = os.getenv("REPORT_DIR", "output")

# timeouts (milliseconds)
DEFAULT_TIMEOUT_MS:   int = int(os.getenv("DEFAULT_TIMEOUT",   "10")) * 1000
PAGE_LOAD_TIMEOUT_MS: int = int(os.getenv("PAGE_LOAD_TIMEOUT", "30")) * 1000

# testing limits
MAX_SECTIONS:       int = int(os.getenv("MAX_SECTIONS_TO_TEST", "20"))
MAX_TESTS_PER_PAGE: int = int(os.getenv("MAX_TESTS_PER_PAGE",  "12"))
MAX_CRAWL_PAGES:    int = int(os.getenv("MAX_CRAWL_PAGES",     "30"))
MAX_SCENARIOS:      int = int(os.getenv("MAX_SCENARIOS",       "15"))
BATCH_SIZE:         int = int(os.getenv("BATCH_SIZE",          "2"))

# llm
LLM_MODEL_FAST:       str   = os.getenv("LLM_MODEL_FAST",       "llama-3.1-8b-instant")
LLM_MODEL_POWERFUL:   str   = os.getenv("LLM_MODEL_POWERFUL",   "llama-3.3-70b-versatile")
LLM_MAX_TOKENS:       int   = int(os.getenv("LLM_MAX_TOKENS",   "2500"))
LLM_BATCH_PAUSE_SEC:  float = float(os.getenv("LLM_BATCH_PAUSE_SEC", "4"))
LLM_RATE_LIMIT_WAIT:  int   = int(os.getenv("LLM_RATE_LIMIT_WAIT",   "65"))
