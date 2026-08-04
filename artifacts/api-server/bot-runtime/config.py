"""
CONFIG - Environment variables, constants, logging, and bot initialization.

This is a runtime copy of the received bot configuration. Secrets are read
only from the environment; no credential fallbacks are kept in this copy.
"""

import logging
import os
import socket
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Dict

from dotenv import load_dotenv
from motor.motor_asyncio import AsyncIOMotorClient
from pymongo import ReturnDocument
from pyrogram import Client


def _install_telegram_utf8_compat():
    """Normalize surrogate-pair escapes before Pyrogram encodes Telegram text."""
    from pyrogram.raw.core.primitives.string import String

    original_new = String.__new__

    def safe_new(cls, value):
        if isinstance(value, str):
            value = value.encode("utf-16", "surrogatepass").decode("utf-16")
        return original_new(cls, value)

    String.__new__ = staticmethod(safe_new)


_install_telegram_utf8_compat()

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
DEPLOYMENT_MODE = os.environ.get("REPLIT_DEPLOYMENT", "").strip().lower()
BOT_SESSION_NAME = os.environ.get(
    "BOT_SESSION_NAME",
    "tg_dl_bot_prod" if DEPLOYMENT_MODE in {"1", "true", "yes"} else "tg_dl_bot_dev",
)


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
POLLING_LOCK_ID = "telegram_polling_lock"
POLLING_LOCK_LEASE_SECONDS = 90
INSTANCE_ID = f"{socket.gethostname()}:{os.getpid()}:{uuid.uuid4().hex}"

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


async def acquire_polling_lock():
    """Claim the single Telegram polling lease for this process."""
    if not has_db():
        logger.error("Cannot start Telegram polling without MongoDB lock storage")
        return False

    now = datetime.now(timezone.utc)
    lease_until = now + timedelta(seconds=POLLING_LOCK_LEASE_SECONDS)
    try:
        await settings_col.update_one(
            {"_id": POLLING_LOCK_ID},
            {
                "$setOnInsert": {
                    "setting": POLLING_LOCK_ID,
                    "owner": None,
                    "lease_until": datetime(1970, 1, 1, tzinfo=timezone.utc),
                }
            },
            upsert=True,
        )
        lock = await settings_col.find_one_and_update(
            {
                "_id": POLLING_LOCK_ID,
                "$or": [
                    {"lease_until": {"$lt": now}},
                    {"lease_until": {"$exists": False}},
                    {"owner": INSTANCE_ID},
                ],
            },
            {
                "$set": {
                    "setting": POLLING_LOCK_ID,
                    "owner": INSTANCE_ID,
                    "lease_until": lease_until,
                    "updated_at": now,
                }
            },
            return_document=ReturnDocument.AFTER,
        )
        acquired = bool(lock and lock.get("owner") == INSTANCE_ID)
        if not acquired:
            current_owner = lock.get("owner") if lock else "unknown"
            logger.warning(
                "Telegram polling lease is held by another instance: %s",
                current_owner,
            )
        return acquired
    except Exception as error:
        logger.error("Could not acquire Telegram polling lease: %s", error)
        return False


async def renew_polling_lock():
    """Renew the lease; false means another process may now poll."""
    if not has_db():
        return False
    now = datetime.now(timezone.utc)
    result = await settings_col.update_one(
        {"_id": POLLING_LOCK_ID, "owner": INSTANCE_ID},
        {
            "$set": {
                "lease_until": now + timedelta(seconds=POLLING_LOCK_LEASE_SECONDS),
                "updated_at": now,
            }
        },
    )
    return result.modified_count == 1


async def release_polling_lock():
    if not has_db():
        return
    try:
        await settings_col.update_one(
            {"_id": POLLING_LOCK_ID, "owner": INSTANCE_ID},
            {"$set": {"owner": None, "lease_until": datetime.now(timezone.utc)}},
        )
    except Exception as error:
        logger.warning("Could not release Telegram polling lease: %s", error)


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
        BOT_SESSION_NAME,
        api_id=API_ID,
        api_hash=API_HASH,
        bot_token=BOT_TOKEN,
        workdir=str(SESSION_DIR),
        workers=16,
        max_concurrent_transmissions=2,
    )