"""
ACTIONS - All action functions (help, status, referral, login, logout, admin, channels, batch)
"""

import logging
from datetime import datetime, timedelta, timezone

from pyrogram import Client
from pyrogram.types import InlineKeyboardMarkup, InlineKeyboardButton
from pyrogram.enums import ChatType

from config import (
    bot, ADMIN_ID, API_ID, API_HASH,
    DAILY_LIMIT, REFERRAL_BONUS, RESET_HOURS, BATCH_MAX,
    user_states, user_clients, has_db, users_col, SESSION_DIR,
)
from keyboards import kb_main, kb_admin, kb_back, kb_cancel
from database import (
    get_user, update_user, get_rem, get_uc,
    has_monthly_plan, enforce_fj, is_admin,
)

logger = logging.getLogger(__name__)


async def act_help(uid):
    await bot.send_message(uid,
        "📚 **Help \u2014 Content Downloader v24.1**\n\n"
        "**\u2705 NEW in v24.1:**\n"
        "\u2022 Fixed: 1 credit per download (not 2)\n"
        "\u2022 24/7 uptime on Render platform\n\n"
        "**\ud83d\udd17 Supported Link Formats:**\n"
        "`t.me/c/CHATID/TOPICID`          \u2192 forum topic\n"
        "`t.me/c/CHATID/TOPICID/MSGID`    \u2192 message in topic\n\n"
        "**\ud83d\udce6 Batch Download:**\n"
        "Send any topic link \u2192 enter count \u2192 choose mode:\n"
        "  1\ufe0f\u20e3 Topic\u2011wise: ONLY that topic's content\n"
        "  2\ufe0f\u20e3 Serial\u2011wise: latest messages in the group\n\n"
        "**\ud83d\udd10 Private groups:** Login required first!\n\n"
        f"\ud83d\udcca {DAILY_LIMIT}/day + {REFERRAL_BONUS}/referral | Max batch: {BATCH_MAX}\n"
        "\u267b\ufe0f Reset every 24h | \ud83d\uddd1 Auto-delete after 30 min",
        reply_markup=kb_back())


async def act_status(uid):
    u  = await get_user(uid)
    r  = await get_rem(uid)
    li = "No \u274c"
    uc = await get_uc(uid)
    if uc:
        try:
            me = await uc.get_me()
            li = f"Yes \u2705 {me.first_name} @{me.username} {me.phone_number}"
        except Exception:
            li = "Error \u26a0\ufe0f"
    try:
        last = datetime.fromisoformat(str(u.get("last_reset", "")))
        if last.tzinfo is None:
            last = last.replace(tzinfo=timezone.utc)
        diff = (last + timedelta(hours=RESET_HOURS)) - datetime.now(timezone.utc)
        rs   = f"{max(0,int(diff.total_seconds()//3600))}h{max(0,int((diff.total_seconds()%3600)//60))}m"
    except Exception:
        rs = "Soon"
    mp_active = await has_monthly_plan(uid)
    mp_line = ""
    if mp_active:
        exp = u.get("monthly_plan_expiry", "")
        mp_line = f"\n\ud83d\udcc5 Monthly Plan: \u2705 Active (expires `{exp[:10]}`)"
        credits_display = "\u267e Unlimited"
    else:
        credits_display = str(r)
    await bot.send_message(uid,
        f"\ud83d\udcca **Status**\n\n\ud83d\udd25 Available Credits: **{credits_display}**\n"
        f"\ud83d\udcc8 Total DL: {u.get('total_downloads', 0)}\n\ud83d\udc65 Referrals: {len(u.get('referrals', []))}\n"
        f"\u23f0 Reset in: {rs}\n\ud83d\udd10 Login: {li}{mp_line}", reply_markup=kb_back())


async def act_referral(uid):
    u  = await get_user(uid)
    bi = await bot.get_me()
    await bot.send_message(uid,
        f"\ud83c\udf81 **Referral Program**\n\n"
        f"\ud83d\udcce `https://t.me/{bi.username}?start=ref_{uid}`\n\n"
        f"\ud83d\udd25 +{REFERRAL_BONUS} credits per friend\n"
        f"\ud83d\udc65 Referred: {len(u.get('referrals', []))}\n"
        f"\ud83d\udc8e Credits earned from referrals: {len(u.get('referrals', [])) * REFERRAL_BONUS}",
        reply_markup=kb_back())


async def act_login(uid):
    u = await get_user(uid)
    if u.get("logged_in"):
        uc = await get_uc(uid)
        if uc:
            try:
                me = await uc.get_me()
                await bot.send_message(uid,
                    f"\u2705 **Logged In**\n{me.first_name} @{me.username} {me.phone_number}",
                    reply_markup=InlineKeyboardMarkup([
                        [InlineKeyboardButton("\ud83d\udd13 Logout", callback_data="btn_logout")],
                        [InlineKeyboardButton("\ud83d\udd19 Back",   callback_data="main_menu")]]))
                return
            except Exception:
                pass
    user_states[uid] = {"step": "phone"}
    await bot.send_message(uid,
        "\ud83d\udcf1 **Login to Telegram**\n\nSend your phone number:\n`+1234567890`\n\nFor OTP use spaces: `1 2 3 4 5`",
        reply_markup=kb_cancel())


async def act_logout(uid):
    if uid in user_clients:
        try:
            await user_clients[uid].stop()
        except Exception:
            pass
        del user_clients[uid]
    await update_user(uid, {"session_string": None, "phone": None, "logged_in": False})


async def act_batch(uid):
    if not await enforce_fj(uid):
        return
    user_states[uid] = {"step": "batch_link"}
    await bot.send_message(uid,
        "\ud83d\udce6 **Batch Download**\n\n"
        "Send a forum topic link:\n"
        "\u2022 `https://t.me/c/CHATID/TOPICID`\n"
        "\u2022 `https://t.me/c/CHATID/TOPICID/MSGID`\n\n"
        "\ud83d\udd0d Auto forum/topic detection enabled!\n"
        f"Max downloads per batch: **{BATCH_MAX}**",
        reply_markup=kb_cancel())


async def act_channels(uid):
    uc = await get_uc(uid)
    if not uc:
        await bot.send_message(uid, "\u26a0\ufe0f Login first to list channels!",
            reply_markup=InlineKeyboardMarkup([
                [InlineKeyboardButton("\ud83d\udd10 Login", callback_data="btn_login")],
                [InlineKeyboardButton("\ud83d\udd19 Back",  callback_data="main_menu")]]))
        return
    sm = await bot.send_message(uid, "\ud83d\udd0d Loading your channels...")
    try:
        chs = []
        async for d in uc.get_dialogs(limit=100):
            if d.chat.type in (ChatType.CHANNEL, ChatType.SUPERGROUP):
                chs.append(d.chat)
        if not chs:
            await sm.edit_text("No channels/supergroups found.", reply_markup=kb_back())
            return
        t = "\ud83d\udcfa **Your Channels / Groups:**\n\n"
        for i, c in enumerate(chs[:20], 1):
            uname_display = '@' + c.username if c.username else '\ud83d\udd12 Private'
            t += f"{i}. **{c.title[:30]}**\n   {uname_display}\n\n"
        await sm.edit_text(t, reply_markup=kb_back())
    except Exception as e:
        await sm.edit_text(f"\u274c Error: {e}", reply_markup=kb_back())


async def act_admin(uid):
    if not await is_admin(uid):
        return
    t  = await users_col.count_documents({}) if has_db() else 0
    a  = await users_col.aggregate(
        [{"$group": {"_id": None, "t": {"$sum": "$total_downloads"}}}]
    ).to_list(1) if has_db() else []
    d  = a[0]["t"] if a else 0
    await bot.send_message(uid,
        f"\ud83d\udc68\u200d\ud83d\udcbc **Admin Panel**\n\n\ud83d\udc65 Users: {t}\n\ud83d\udd25 Total DL: {d}\n\ud83d\udd17 Active: {len(user_clients)}\n"
        "\ud83d\udc41 Shadow monitoring: ON\n\n"
        "`/credit <id> <n>` | `/replyuser <id> <msg>`\n"
        "`/broadcast` (reply) | `/setjoin` `/removejoin`",
        reply_markup=kb_admin())
