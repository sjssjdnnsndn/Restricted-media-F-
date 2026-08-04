"""
TELEGRAM CONTENT DOWNLOADER BOT v24.1 - MODULAR VERSION
=========================================================
Clean entry point. All logic is split into modules:

  config.py          - ENV, constants, logging, bot init, MongoDB, shared state
  keyboards.py       - All InlineKeyboardMarkup functions
  helpers.py         - URL parsing, chat ID helpers, content type, safe wrappers
  database.py        - User CRUD, payments, force join, settings, credits
  downloader.py      - Progress bar, shadow, access check, message fetching
  sender.py          - Content sender, batch download
  actions.py         - Action functions (help, status, login, admin, etc.)
  commands.py        - All /command handlers
  callbacks.py       - Master callback router
  callbacks_main.py  - Menu, cancel, verify, mode selection callbacks
  callbacks_payment.py - Payment callbacks (UPI, crypto, monthly)
  callbacks_admin.py - Admin panel callbacks
  message_handler.py - Message handler (admin setup, payment, login, support)
  link_handler.py    - Batch flow, single link download
  web_server.py      - Health check, keep-alive, cleanup, mongo keepalive
"""

import asyncio
import logging

from pyrogram.types import ChatMemberUpdated
from pyrogram.enums import ChatMemberStatus

from config import (
    bot,
    _missing,
    DB_NAME,
    acquire_polling_lock,
    renew_polling_lock,
    release_polling_lock,
    POLLING_LOCK_LEASE_SECONDS,
)
from database import get_fj
from web_server import start_web, cleanup_loop, mongo_keepalive, keep_alive_ping

# Import handlers so they register with @bot decorators
import commands        # noqa: F401
import callbacks       # noqa: F401
import message_handler # noqa: F401

logger = logging.getLogger(__name__)


async def polling_lock_heartbeat():
    """Keep the single-poller lease alive and stop polling if it is lost."""
    interval = max(10, POLLING_LOCK_LEASE_SECONDS // 3)
    while True:
        await asyncio.sleep(interval)
        try:
            if await renew_polling_lock():
                logger.debug("Telegram polling lease renewed")
                continue
            logger.error("Telegram polling lease lost; stopping this poller")
            try:
                await bot.stop()
            except Exception as error:
                logger.error("Could not stop poller after lease loss: %s", error)
            return
        except Exception as error:
            logger.error("Telegram polling lease renewal failed: %s", error)


# ═══════════════════════════════════════════
# FORCE JOIN - Member update handler
# ═══════════════════════════════════════════
@bot.on_chat_member_updated()
async def on_member_update(_, update: ChatMemberUpdated):
    """Detect when a user leaves/is kicked from the force-join channel."""
    try:
        fid, fu = await get_fj()
        if not fid:
            return

        chat_id = update.chat.id
        if abs(chat_id) != abs(int(fid)):
            return

        old_chat_member = getattr(update, 'old_chat_member', None)
        new_chat_member = getattr(update, 'new_chat_member', None)

        old_status = old_chat_member.status if old_chat_member else None
        new_status = new_chat_member.status if new_chat_member else None

        was_member = old_status in (
            ChatMemberStatus.MEMBER, ChatMemberStatus.OWNER,
            ChatMemberStatus.ADMINISTRATOR, ChatMemberStatus.RESTRICTED
        )
        has_left = new_status in (ChatMemberStatus.LEFT, ChatMemberStatus.BANNED)

        if not (was_member and has_left):
            return

        user = new_chat_member.user if new_chat_member else None
        if not user or user.is_bot:
            return

        uid = user.id
        channel_ref = f"@{fu}" if fu else "our channel"
        logger.info(f"[fj] uid={uid} left {channel_ref} - sending goodbye")

        try:
            await bot.send_message(
                uid,
                f"\ud83d\udc4b **Goodbye!**\n"
                f"You left our channel, \"{channel_ref}\".\n\n"
                f"You're always welcome to come back anytime! \ud83d\ude42"
            )
        except Exception as e:
            logger.info(f"[fj] Could not DM goodbye to uid={uid}: {e}")

    except Exception as e:
        logger.warning(f"[fj] on_member_update error: {e}")


# ═══════════════════════════════════════════
# MAIN
# ═══════════════════════════════════════════
async def main():
    if _missing:
        logger.error(f"Missing env vars: {', '.join(_missing)}")
        await start_web()
        await asyncio.Event().wait()
        return

    # Pyrogram's decorator-based handlers are queued asynchronously by
    # add_handler(). Let those registration tasks finish before start()
    # initializes the dispatcher; otherwise the client can connect to
    # Telegram while having zero message handlers.
    await asyncio.sleep(0)
    await asyncio.sleep(0.05)
    registered_handlers = sum(
        len(group) for group in bot.dispatcher.groups.values()
    )
    logger.info(
        "Registered Telegram handlers: groups=%s handlers=%s",
        len(bot.dispatcher.groups),
        registered_handlers,
    )
    if registered_handlers == 0:
        raise RuntimeError("No Telegram handlers registered before bot startup")

    logger.info("=" * 60)
    logger.info("BOT v24.1 - MODULAR VERSION")
    logger.info("Credits fix + Keep-alive + Modular split")
    logger.info("=" * 60)

    await start_web()
    if not await acquire_polling_lock():
        logger.warning("This instance is standby-only; Telegram polling will not start")
        await asyncio.Event().wait()
        return

    logger.info("Telegram polling lease acquired")
    asyncio.create_task(cleanup_loop())
    asyncio.create_task(mongo_keepalive())
    asyncio.create_task(keep_alive_ping())
    asyncio.create_task(polling_lock_heartbeat())
    try:
        await bot.start()
        me = await bot.get_me()
        logger.info(f"@{me.username} | DB: {DB_NAME}")
        logger.info("BOT IS RUNNING!")
        await asyncio.Event().wait()
    finally:
        await release_polling_lock()


if __name__ == "__main__":
    try:
        if bot is not None:
            bot.run(main())
        else:
            logger.error("Bot not initialized - check BOT_TOKEN, API_ID, API_HASH!")
    except KeyboardInterrupt:
        logger.info("Bot stopped by user.")
