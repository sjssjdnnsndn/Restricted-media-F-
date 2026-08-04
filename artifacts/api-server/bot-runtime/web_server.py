"""
WEB SERVER - Health check endpoint, keep-alive ping, cleanup loop, mongo keepalive
"""

import asyncio
import logging
from datetime import datetime, timedelta, timezone

from aiohttp import web

from config import (
    bot, PORT, RENDER_URL, MONGO_URL, DB_NAME,
    has_db, db, users_col, tickets_col, settings_col,
)

logger = logging.getLogger(__name__)


# ═══════════════════════════════════════════
# WEB SERVER
# ═══════════════════════════════════════════
async def start_web():
    try:
        app    = web.Application()
        app.router.add_get("/",       lambda r: web.Response(text="OK"))
        app.router.add_get("/health", lambda r: web.Response(text="OK"))
        # The API artifact is mounted at /api in the published router.
        # Keep the received bot source untouched while exposing the same
        # health response through the public artifact path.
        app.router.add_get("/api/health", lambda r: web.Response(text="OK"))
        runner = web.AppRunner(app)
        await runner.setup()
        await web.TCPSite(runner, "0.0.0.0", PORT).start()
        logger.info(f"Web server running on port {PORT}")
    except Exception as e:
        logger.error(f"Web server error: {e}")


# ═══════════════════════════════════════════
# CLEANUP LOOP (auto-delete after 30 min)
# ═══════════════════════════════════════════
async def cleanup_loop():
    while True:
        try:
            await asyncio.sleep(120)
            if not has_db():
                continue
            cutoff = (datetime.now(timezone.utc) - timedelta(minutes=30)).isoformat()
            docs   = await db.downloaded_content.find({"timestamp": {"$lt": cutoff}}).to_list(100)
            for d in docs:
                for mid in d.get("bot_message_ids", []):
                    try:
                        await bot.delete_messages(d["user_id"], mid)
                    except Exception:
                        pass
                    await asyncio.sleep(0.1)
                await db.downloaded_content.delete_one({"_id": d["_id"]})
        except Exception:
            await asyncio.sleep(60)


# ═══════════════════════════════════════════
# MONGO KEEPALIVE
# ═══════════════════════════════════════════
async def mongo_keepalive():
    import config
    from motor.motor_asyncio import AsyncIOMotorClient
    while True:
        try:
            await asyncio.sleep(300)
            if has_db():
                await users_col.find_one({})
                logger.info("Mongo keepalive OK")
        except Exception:
            try:
                config.mongo_client = AsyncIOMotorClient(MONGO_URL)
                config.db           = config.mongo_client[DB_NAME]
                config.users_col    = config.db.users
                config.tickets_col  = config.db.support_tickets
                config.settings_col = config.db.bot_settings
                config._db_ok       = True
                logger.info("Mongo reconnected")
            except Exception as e:
                logger.error(f"Mongo reconnect failed: {e}")
                config._db_ok = False


# ═══════════════════════════════════════════
# KEEP-ALIVE PING FOR RENDER (every 12s)
# ═══════════════════════════════════════════
async def keep_alive_ping():
    import aiohttp
    local_url  = f"http://localhost:{PORT}/health"
    interval   = 12
    public_url = (RENDER_URL + "/health") if RENDER_URL else None

    public_status = public_url if public_url else "NOT DETECTED (not on Render?)"
    logger.info(
        f"\ud83d\udd04 Keep-alive started (every {interval}s) | "
        f"local={local_url} | public={public_status}"
    )

    while True:
        try:
            await asyncio.sleep(interval)
            timeout = aiohttp.ClientTimeout(total=8)
            async with aiohttp.ClientSession(timeout=timeout) as session:

                # Ping 1: localhost
                try:
                    async with session.get(local_url) as resp:
                        if resp.status == 200:
                            logger.debug("\u2705 Keep-alive local OK")
                        else:
                            logger.warning(f"\u26a0\ufe0f Keep-alive local HTTP {resp.status}")
                except Exception as e:
                    logger.debug(f"Keep-alive local error: {type(e).__name__}")

                # Ping 2: Render public URL
                if public_url:
                    try:
                        async with session.get(public_url) as resp:
                            if resp.status == 200:
                                logger.debug("\u2705 Keep-alive public OK")
                            else:
                                logger.warning(f"\u26a0\ufe0f Keep-alive public HTTP {resp.status}")
                    except Exception as e:
                        logger.debug(f"Keep-alive public error: {type(e).__name__}")

        except asyncio.CancelledError:
            logger.info("\ud83d\uded1 Keep-alive ping stopped")
            break
        except Exception as e:
            logger.debug(f"Keep-alive outer error: {type(e).__name__}: {e}")
