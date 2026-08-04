"""
CONFIG - Environment variables, constants, logging, and bot initialization.

This is a runtime copy of the received bot configuration. Secrets are read
only from the environment; no credential fallbacks are kept in this copy.
"""

import logging
import os
from pathlib import Path
from typing import Dict

from dotenv import load_dotenv
from motor.motor_asyncio import AsyncIOMotorClient
from pyrogram import Client

load_dotenv()

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
)
logger = logging.getLogger(__name__)
logging.getLogger("pyrogram").setLevel(logging.WARNING)
logging.getLogger("pyrogram.session").setLevel(logging.WARNING)
logging.getLogger("pyrogram.dispatcher").setLevel(logging.ERROR)

BOT_TOKEN = os.environ.get("BOT_TOKEN", "").strip()
API_ID = int(os.environ.get("API_ID", "0"))
API_HASH = os.environ.get("API_HASH", "").strip()
ADMIN_ID = int(os.environ.get("ADMIN_ID", "0"))
MONGO_URL = os.environ.get("MONGO_URL", "").strip()
DB_NAME = os.environ.get("DB_NAME", "BDL")
PORT = int(os.environ.get("PORT", "8080"))


def _detect_public_url() -> str:
    configured = os.environ.get("RENDER_URL", "").strip()
    if configured:
        return configured.rstrip("/")
    return ""


RENDER_URL = _detect_public_url()

DAILY_LIMIT = 4
REFERRAL_BONUS = 4
RESET_HOURS = 24
BATCH_MAX = 2000

_missing = []
if not BOT_TOKEN:
    _missing.append("BOT_TOKEN")
if not API_ID:
    _missing.append("API_ID")
if not API_HASH:
    _missing.append("API_HASH")
if not MONGO_URL:
    _missing.append("MONGO_URL")
if _missing:
    logger.error("MISSING ENV: %s", ", ".join(_missing))

BASE_DIR = Path(__file__).parent
SESSION_DIR = BASE_DIR / "sessions"
SESSION_DIR.mkdir(parents=True, exist_ok=True)

mongo_client = None
db = None
users_col = None
tickets_col = None
settings_col = None
_db_ok = False

if MONGO_URL:
    try:
        mongo_client = AsyncIOMotorClient(MONGO_URL)
        db = mongo_client[DB_NAME]
        users_col = db.users
        tickets_col = db.support_tickets
        settings_col = db.bot_settings
        _db_ok = True
        logger.info("MongoDB client initialized for database %s", DB_NAME)
    except Exception as error:
        logger.error("MongoDB init error: %s", error)


def has_db():
    return _db_ok


user_states: Dict[int, dict] = {}
user_clients: Dict[int, Client] = {}
waiting_invite: Dict[int, dict] = {}
dl_cancel: Dict[int, bool] = {}

MAX_BOT_FILE_SIZE = 2 * 1024 * 1024 * 1024
MAX_USER_FILE_SIZE = 4 * 1024 * 1024 * 1024
TIMEOUT_PER_GB = 1800
BASE_DL_TIMEOUT = 300
BASE_UL_TIMEOUT = 300
MAX_TIMEOUT = 7200
LARGE_FILE_RETRIES = 3
RETRY_DELAY = 5
STALL_TIMEOUT = 120


def calc_timeout(file_size_bytes: int, base: int = 300) -> int:
    if not file_size_bytes or file_size_bytes <= 0:
        return base
    size_gb = file_size_bytes / (1024 * 1024 * 1024)
    timeout = base + int(size_gb * TIMEOUT_PER_GB)
    return min(timeout, MAX_TIMEOUT)


bot = None
if BOT_TOKEN and API_ID and API_HASH:
    bot = Client(
        "tg_dl_bot",
        api_id=API_ID,
        api_hash=API_HASH,
        bot_token=BOT_TOKEN,
        workdir=str(SESSION_DIR),
        workers=16,
        max_concurrent_transmissions=2,
    )