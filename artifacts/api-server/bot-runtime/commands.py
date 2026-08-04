"""
COMMANDS - All /command handlers (start, help, status, login, logout, batch, admin, credit, etc.)
"""

import asyncio
import logging

from pyrogram import filters
from pyrogram.types import InlineKeyboardMarkup, InlineKeyboardButton

from config import (
    bot, ADMIN_ID, REFERRAL_BONUS,
    user_states, waiting_invite, dl_cancel,
    has_db, users_col,
)
from keyboards import kb_main, kb_admin
from database import (
    get_user, get_rem, do_referral, enforce_fj,
    rm_fj, is_admin,
)
from actions import (
    act_help, act_status, act_referral, act_login,
    act_logout, act_batch, act_channels, act_admin,
)

logger = logging.getLogger(__name__)


@bot.on_message(filters.command("start") & filters.private)
async def cmd_start(_, m):
    uid  = m.from_user.id
    if not await enforce_fj(uid):
        return
    await get_user(uid)
    args = m.text.split()
    if len(args) > 1 and args[1].startswith("ref_"):
        try:
            rid = int(args[1][4:])
            if rid != uid and await do_referral(rid, uid):
                try:
                    await bot.send_message(rid, f"\ud83c\udf89 +{REFERRAL_BONUS} credits from referral!")
                except Exception:
                    pass
        except Exception:
            pass
    r = await get_rem(uid)
    await m.reply_text(
        f"\ud83d\ude80 **Telegram Content Downloader v23**\n\n\ud83d\udd25 **{r}** credits available\n\n"
        "\u2022 Public & private groups\n"
        "\u2022 Auto forum/topic detection\n"
        "\u2022 Batch topic-wise or serial-wise\n"
        "\u2022 ALL content types supported\n\n"
        "Send a link or use \ud83d\udce6 Batch! \ud83d\udc47",
        reply_markup=kb_main(), disable_web_page_preview=True)
    if await is_admin(uid):
        await m.reply_text("\ud83d\udc68\u200d\ud83d\udcbc Admin panel:", reply_markup=InlineKeyboardMarkup(
            [[InlineKeyboardButton("\ud83d\udc68\u200d\ud83d\udcbc Admin", callback_data="btn_admin")]]))


@bot.on_message(filters.command("help") & filters.private)
async def cmd_help(_, m):
    await act_help(m.from_user.id)


@bot.on_message(filters.command("status") & filters.private)
async def cmd_status(_, m):
    await act_status(m.from_user.id)


@bot.on_message(filters.command("login") & filters.private)
async def cmd_login(_, m):
    await act_login(m.from_user.id)


@bot.on_message(filters.command("logout") & filters.private)
async def cmd_logout(_, m):
    await act_logout(m.from_user.id)
    await m.reply_text("\ud83d\udc4b Logged out!", reply_markup=kb_main())


@bot.on_message(filters.command("referral") & filters.private)
async def cmd_ref(_, m):
    await act_referral(m.from_user.id)


@bot.on_message(filters.command("batch") & filters.private)
async def cmd_batch(_, m):
    await act_batch(m.from_user.id)


@bot.on_message(filters.command("cancel") & filters.private)
async def cmd_cancel(_, m):
    user_states.pop(m.from_user.id, None)
    waiting_invite.pop(m.from_user.id, None)
    dl_cancel[m.from_user.id] = True
    await m.reply_text("\u274c Cancelled!", reply_markup=kb_main())


@bot.on_message(filters.command("mychannels") & filters.private)
async def cmd_ch(_, m):
    await act_channels(m.from_user.id)


@bot.on_message(filters.command("admin") & filters.private)
async def cmd_adm(_, m):
    if await is_admin(m.from_user.id):
        await act_admin(m.from_user.id)


@bot.on_message(filters.command("credit") & filters.private)
async def cmd_credit(_, m):
    if not await is_admin(m.from_user.id):
        return
    try:
        p   = m.text.split()
        tid = int(p[1])
        amt = int(p[2])
        if amt < 1 or amt > 1000:
            raise ValueError
        if not has_db():
            return
        u = await users_col.find_one({"user_id": tid})
        if not u:
            await m.reply_text("User not found")
            return
        await users_col.update_one({"user_id": tid}, {"$inc": {"bonus_downloads": amt}})
        await m.reply_text(f"\u2705 +{amt} credits \u2192 `{tid}`")
        try:
            await bot.send_message(tid, f"\ud83c\udf81 +{amt} credits added to your account!")
        except Exception:
            pass
    except Exception:
        await m.reply_text("`/credit <user_id> <amount>`")


@bot.on_message(filters.command("reduce") & filters.private)
async def cmd_reduce(_, m):
    if not await is_admin(m.from_user.id):
        return
    try:
        p   = m.text.split()
        tid = int(p[1])
        amt = int(p[2])
        if amt < 1 or amt > 10000:
            raise ValueError
        if not has_db():
            return
        u = await users_col.find_one({"user_id": tid})
        if not u:
            await m.reply_text("\u274c User not found")
            return
        current_bonus = u.get("bonus_downloads", 0)
        new_bonus = max(0, current_bonus - amt)
        deducted = current_bonus - new_bonus
        await users_col.update_one({"user_id": tid}, {"$set": {"bonus_downloads": new_bonus}})
        un = u.get("username", "N/A") if isinstance(u.get("username"), str) else "N/A"
        await m.reply_text(
            f"\u2705 **Credits Reduced**\n\n"
            f"\ud83d\udc64 User: `{tid}` | @{un}\n"
            f"\u2796 Deducted: **{deducted}** credits\n"
            f"\ud83d\udc8e Remaining credits: **{new_bonus}**",
            reply_markup=kb_admin())
        try:
            await bot.send_message(tid, f"\u26a0\ufe0f **{deducted} credits** have been deducted from your account by admin.")
        except Exception:
            pass
    except Exception:
        await m.reply_text("`/reduce <user_id> <amount>`")


@bot.on_message(filters.command("replyuser") & filters.private)
async def cmd_reply(_, m):
    if not await is_admin(m.from_user.id):
        return
    p = m.text.split(maxsplit=2)
    if len(p) < 3:
        await m.reply_text("`/replyuser <id> <message>`")
        return
    try:
        await bot.send_message(int(p[1]), f"\ud83d\udce9 **Message from Admin:**\n\n{p[2]}")
        await m.reply_text("\u2705 Sent!")
    except Exception as e:
        await m.reply_text(f"\u274c {e}")


@bot.on_message(filters.command("setjoin") & filters.private)
async def cmd_sj(_, m):
    if not await is_admin(m.from_user.id):
        return
    user_states[m.from_user.id] = {"step": "set_fj"}
    await m.reply_text("Send @username or -100ID or forward a message from the channel")


@bot.on_message(filters.command("removejoin") & filters.private)
async def cmd_rj(_, m):
    if not await is_admin(m.from_user.id):
        return
    await rm_fj()
    await m.reply_text("\u2705 Force join removed!")


@bot.on_message(filters.command("broadcast") & filters.private)
async def cmd_bc(_, m):
    if not await is_admin(m.from_user.id) or not m.reply_to_message:
        await m.reply_text("Reply to a message to broadcast it")
        return
    st = await m.reply_text("\ud83d\udce1 Broadcasting...")
    if not has_db():
        return
    ul = await users_col.find({}).to_list(None)
    s = f = 0
    ref = m.reply_to_message
    has_photo = ref.photo is not None
    photo_id  = ref.photo.file_id if has_photo else None
    caption   = (ref.caption or ref.text or "").strip()
    for u in ul:
        try:
            if has_photo:
                await bot.send_photo(u["user_id"], photo_id,
                    caption=f"\ud83d\udce2 {caption}" if caption else None)
            else:
                await bot.send_message(u["user_id"], f"\ud83d\udce2 {caption}")
            s += 1
            await asyncio.sleep(0.05)
        except Exception:
            f += 1
    await st.edit_text(f"\u2705 Sent: {s} | \u274c Failed: {f}")
