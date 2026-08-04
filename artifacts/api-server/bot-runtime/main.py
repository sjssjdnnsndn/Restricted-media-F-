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

from config import bot, _missing, DB_NAME
from database import get_fj
from web_server import start_web, cleanup_loop, mongo_keepalive, keep_alive_ping

# Import handlers so they register with @bot decorators
import commands        # noqa: F401
import callbacks       # noqa: F401
import message_handler # noqa: F401

logger = logging.getLogger(__name__)


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

    logger.info("=" * 60)
    logger.info("BOT v24.1 - MODULAR VERSION")
    logger.info("Credits fix + Keep-alive + Modular split")
    logger.info("=" * 60)

    await start_web()
    asyncio.create_task(cleanup_loop())
    asyncio.create_task(mongo_keepalive())
    asyncio.create_task(keep_alive_ping())
    await bot.start()
    me = await bot.get_me()
    logger.info(f"@{me.username} | DB: {DB_NAME}")
    logger.info("BOT IS RUNNING!")
    await asyncio.Event().wait()


if __name__ == "__main__":
    try:
        if bot is not None:
            bot.run(main())
        else:
            logger.error("Bot not initialized - check BOT_TOKEN, API_ID, API_HASH!")
    except KeyboardInterrupt:
        logger.info("Bot stopped by user.")
