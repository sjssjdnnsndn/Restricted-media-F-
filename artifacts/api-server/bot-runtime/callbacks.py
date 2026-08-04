"""
CALLBACKS - Master callback router
Routes callback queries to the correct handler module.
"""

import logging

from pyrogram.types import CallbackQuery

from config import bot
from callbacks_main import handle_main_callbacks
from callbacks_payment import handle_payment_callbacks
from callbacks_admin import handle_admin_callbacks

logger = logging.getLogger(__name__)


@bot.on_callback_query()
async def on_cb(_, q: CallbackQuery):
    uid = q.from_user.id
    d   = q.data
    try:
        # 1. Main menu, cancel, verify, mode selection, simple buttons
        if await handle_main_callbacks(q, uid, d):
            return

        # 2. Payment callbacks (UPI, crypto, monthly, approve/reject)
        if await handle_payment_callbacks(q, uid, d):
            return

        # 3. Admin panel callbacks
        if await handle_admin_callbacks(q, uid, d):
            return

    except Exception as e:
        logger.error(f"Callback error: {e}")
        try:
            await q.answer("\u274c Error", show_alert=True)
        except Exception:
            pass
