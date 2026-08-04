"""
CALLBACKS MAIN - Menu, cancel, verify join, batch mode selection, simple button actions
"""

import asyncio
import logging

from pyrogram.types import InlineKeyboardMarkup, InlineKeyboardButton

from config import (
    bot, user_states, waiting_invite, dl_cancel,
)
from keyboards import kb_main, kb_cancel
from database import get_fj, in_ch
from actions import (
    act_status, act_help, act_login, act_referral,
    act_batch, act_channels, act_admin, act_logout,
)
from sender import run_batch

logger = logging.getLogger(__name__)


async def handle_main_callbacks(q, uid, d):
    """
    Handle main menu callbacks. Returns True if handled, False otherwise.
    """

    # Cancel download
    if d.startswith("cancel_dl_"):
        try:
            tid = int(d.replace("cancel_dl_", ""))
        except Exception:
            await q.answer("\u274c")
            return True
        if tid != uid:
            await q.answer("Not your download!", show_alert=True)
            return True
        dl_cancel[uid] = True
        try:
            await q.message.delete()
        except Exception:
            pass
        await bot.send_message(uid, "\u274c Download cancelled!", reply_markup=kb_main())
        await q.answer("Cancelled")
        return True

    # Cancel button
    if d == "btn_cancel":
        user_states.pop(uid, None)
        waiting_invite.pop(uid, None)
        dl_cancel[uid] = True
        try:
            await q.message.edit_text("\u274c Cancelled!", reply_markup=kb_main())
        except Exception:
            await bot.send_message(uid, "\u274c Cancelled!", reply_markup=kb_main())
        return True

    # Verify join
    if d == "verify_join":
        fid, _ = await get_fj()
        if fid and await in_ch(uid, fid):
            try:
                await q.message.delete()
            except Exception:
                pass
            await bot.send_message(uid, "\u2705 Verified! Welcome!", reply_markup=kb_main())
        else:
            await q.answer("\u274c Not joined yet!", show_alert=True)
        return True

    # Main menu
    if d == "main_menu":
        user_states.pop(uid, None)
        try:
            await q.message.edit_text("\ud83c\udfe0 **Main Menu**", reply_markup=kb_main())
        except Exception:
            await bot.send_message(uid, "\ud83c\udfe0 Main Menu", reply_markup=kb_main())
        return True

    # Forward-choice: user picks "No, send to me" → start batch immediately (send to user)
    if d == "fwd_no":
        st = user_states.get(uid)
        if not st or st.get("step") != "batch_forward_choice":
            await q.answer("❌ Session expired. Please start again.", show_alert=True)
            return True
        pi           = st["pi"]
        chat_id      = st["chat_id"]
        count        = st["count"]
        mode         = st["mode"]
        title        = st["title"]
        dl           = st["dl"]
        un           = st.get("un")
        fn           = st.get("fn", "?")
        start_msg_id = st.get("start_msg_id")
        user_states.pop(uid, None)

        try:
            await q.message.edit_text(
                f"✅ **Sending to you directly**\n"
                f"⏳ Starting download of **{count}** posts from **{title}**...",
                reply_markup=kb_cancel(uid))
        except Exception:
            await bot.send_message(uid, "⏳ Starting download...", reply_markup=kb_cancel(uid))
        await q.answer()
        asyncio.create_task(
            run_batch(uid, un, fn, pi, chat_id, count, mode, title, dl,
                      start_from_msg_id=start_msg_id, forward_to=None))
        return True

    # Forward-choice: user picks "Forward to my Channel/Group" → ask for channel id/link
    if d == "fwd_yes":
        st = user_states.get(uid)
        if not st or st.get("step") != "batch_forward_choice":
            await q.answer("❌ Session expired. Please start again.", show_alert=True)
            return True
        # Keep all batch params, just change the step
        st["step"] = "batch_forward_channel"
        user_states[uid] = st
        try:
            await q.message.edit_text(
                "📤 **Forward Mode Selected**\n\n"
                "Please send me your channel/group in **any** of these ways:\n"
                "• `@username` — public channel/group\n"
                "• `-100XXXXXXXXXX` — numeric chat ID (for private)\n"
                "• Or **forward any message** from that channel/group\n\n"
                "⚠️ **Important:** Add this bot as an **Admin** in that channel/group "
                "with permission to *post messages*. Otherwise forwarding will fail.",
                reply_markup=kb_cancel())
        except Exception:
            await bot.send_message(uid,
                "📤 Send your channel/group as @username, -100 ID, or forward a message from it. "
                "Make sure the bot is Admin there.",
                reply_markup=kb_cancel())
        await q.answer()
        return True

    # Batch mode selection: Topic-wise or Serial-wise
    if d in ("bm_topic", "bm_serial"):
        st = user_states.get(uid)
        if not st or st.get("step") != "batch_mode":
            await q.answer("\u274c Session expired. Please start again.", show_alert=True)
            return True
        mode = "topic" if d == "bm_topic" else "serial"
        pi   = st["pi"]
        ci   = st["ci"]
        cnt  = st["cnt"]
        tit  = st["tit"]
        uc   = st["uc"]
        un   = st.get("un")
        fn   = st.get("fn", "?")
        tid  = pi.get("topic_id")
        user_states.pop(uid, None)

        lbl = "Topic-wise" if mode == "topic" else "Serial-wise"
        await q.message.edit_text(
            f"\u2705 **{lbl}** selected"
            + (f" | Topic `{tid}`" if mode == "topic" and tid else "")
            + "\n\u23f3 Starting download...",
            reply_markup=kb_cancel(uid))
        await q.answer()
        asyncio.create_task(run_batch(uid, un, fn, pi, ci, cnt, mode, tit, uc))
        return True

    # Simple action buttons
    simple = {
        "btn_status":     lambda: act_status(uid),
        "btn_help":       lambda: act_help(uid),
        "btn_login":      lambda: act_login(uid),
        "btn_referral":   lambda: act_referral(uid),
        "btn_batch":      lambda: act_batch(uid),
        "btn_mychannels": lambda: act_channels(uid),
        "btn_admin":      lambda: act_admin(uid),
    }
    if d in simple:
        await q.answer()
        await simple[d]()
        return True

    # Logout
    if d == "btn_logout":
        await q.answer()
        await act_logout(uid)
        await bot.send_message(uid, "\ud83d\udc4b Logged out!", reply_markup=kb_main())
        return True

    # Support
    if d == "btn_support":
        user_states[uid] = {"step": "support"}
        try:
            await q.message.edit_text("\ud83d\udcac Describe your issue:", reply_markup=kb_cancel())
        except Exception:
            await bot.send_message(uid, "\ud83d\udcac Describe your issue:", reply_markup=kb_cancel())
        return True

    # Deposit
    if d == "btn_deposit":
        await q.answer()
        deposit_text = (
            "**Deposit - Buy Credits**\n\n"
            "Choose your payment method:"
        )
        deposit_kb = InlineKeyboardMarkup([
            [InlineKeyboardButton("UPI Payment", callback_data="pay_upi"),
             InlineKeyboardButton("Crypto", callback_data="pay_crypto")],
            [InlineKeyboardButton("Monthly Plan (Unlimited)", callback_data="pay_monthly")],
            [InlineKeyboardButton("Back", callback_data="main_menu")]
        ])
        try:
            await q.message.edit_text(deposit_text, reply_markup=deposit_kb)
        except Exception as e:
            logger.error(f"[deposit] edit_text failed: {e}")
            try:
                await bot.send_message(uid, deposit_text, reply_markup=deposit_kb)
            except Exception as e2:
                logger.error(f"[deposit] send_message also failed: {e2}")
        return True

    return False
