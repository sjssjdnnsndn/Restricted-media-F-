"""
LINK HANDLER - Batch flow (link, start link, count), single link download, process_links

FLOW LOGIC:
===========
A) BATCH MODE (user clicks Batch button -> step=batch_link):
   Step 1 (batch_link): User sends topic/chat link
     -> If topic detected: ask for starting point link (step=batch_start_link)
     -> If not topic: ask for count directly (step=batch_count)
   Step 2 (batch_start_link): User sends specific post link as starting point
     -> Ask for count (step=batch_count)
   Step 3 (batch_count): User sends number
     -> Start downloading from starting point, N posts forward

B) SINGLE LINK (user sends a link directly, no batch mode):
   - 3-part URL (t.me/c/CHAT/TOPIC/MSG): download that ONE specific message
   - 2-part URL with forum detected: ask for starting point + count (auto-batch)
   - 2-part URL without forum: download that ONE specific message
   - Multiple links: download each one individually
"""

import asyncio
import logging
from datetime import datetime, timezone

from pyrogram.types import InlineKeyboardMarkup, InlineKeyboardButton

from config import (
    bot, BATCH_MAX,
    user_states, dl_cancel, has_db, db,
)
from keyboards import kb_main, kb_cancel, kb_no_credits, kb_forward_choice
from database import get_rem, use_credit, get_uc, enforce_fj
from helpers import (
    extract_post_info, detect_and_fix_forum,
    safe_get_chat, get_ct, build_link,
)
from downloader import fetch_one, check_access
from sender import send_content, run_batch

logger = logging.getLogger(__name__)


async def handle_links_and_batch(message, uid, text):
    """
    Handles batch flow steps (link, start_link, count) and single link downloads.
    Called from message_handler.py when no other state matches.
    """

    # ── BATCH Step 1: user sends topic/chat link ──
    if user_states.get(uid, {}).get("step") == "batch_link":
        info = extract_post_info(text)
        if not info:
            await message.reply_text(
                "\u274c Invalid link!\n\nExpected:\n"
                "\u2022 `https://t.me/c/CHATID/TOPICID`\n"
                "\u2022 `https://t.me/c/CHATID/TOPICID/MSGID`",
                reply_markup=kb_cancel())
            return

        logger.info(f"BATCH LINK parsed: {info}")

        # Run forum detection
        uc = await get_uc(uid)
        detect_client = uc if uc else (bot if not info.get("is_private") else None)
        if detect_client and info.get("needs_forum_check"):
            info = await detect_and_fix_forum(detect_client, info)

        logger.info(f"BATCH LINK after forum check: {info}")

        user_states[uid]["pi"]   = info
        user_states[uid]["link"] = text

        tid      = info.get("topic_id")
        is_topic = info.get("is_topic", False)

        if is_topic and tid:
            # Topic detected -> ask for starting point
            user_states[uid]["step"] = "batch_start_link"
            await message.reply_text(
                f"\u2705 **Link accepted!**\n"
                f"\u2705 **Forum topic detected!** (Topic ID: `{tid}`)\n\n"
                f"\ud83d\udccc **Now send the link of the specific post from which you want to start downloading.**\n"
                f"_(Send any message link from inside this topic)_",
                reply_markup=kb_cancel())
        else:
            # Not a topic -> ask for count directly (serial mode)
            user_states[uid]["step"] = "batch_count"
            await message.reply_text(
                f"\u2705 **Link accepted!**\n\n"
                f"How many posts to download? (1\u2013{BATCH_MAX}):",
                reply_markup=kb_cancel())
        return

    # ── BATCH Step 2 (topic only): user sends starting point link ──
    if user_states.get(uid, {}).get("step") == "batch_start_link":
        start_info = extract_post_info(text)
        state = user_states.get(uid, {})
        pi    = state.get("pi", {})
        tid   = pi.get("topic_id")

        if not start_info:
            await message.reply_text(
                "\u274c **Invalid link!**\n\n"
                "Please send a valid message link from inside the topic, like:\n"
                "`https://t.me/c/CHATID/TOPICID/MSGID`",
                reply_markup=kb_cancel())
            return

        start_msg_id = start_info.get("message_id") or start_info.get("number")
        start_chat   = start_info.get("chat_id") or start_info.get("username")
        topic_chat   = pi.get("chat_id") or pi.get("username")

        # Validate same chat
        try:
            if abs(int(start_chat or 0)) != abs(int(topic_chat or 0)):
                await message.reply_text(
                    "\u274c **Wrong chat!**\n\n"
                    "The file link must be from the **same group** as the topic link.\n"
                    "Please send the correct starting file link:",
                    reply_markup=kb_cancel())
                return
        except (ValueError, TypeError):
            # String usernames comparison
            if str(start_chat) != str(topic_chat):
                await message.reply_text(
                    "\u274c **Wrong chat!**\n\n"
                    "The file link must be from the **same group** as the topic link.",
                    reply_markup=kb_cancel())
                return

        if not start_msg_id:
            await message.reply_text(
                "\u274c Could not extract message ID from the link. Please try again:",
                reply_markup=kb_cancel())
            return

        logger.info(f"BATCH START LINK: topic={tid} start_msg_id={start_msg_id} chat={start_chat}")

        user_states[uid]["start_msg_id"] = start_msg_id
        user_states[uid]["step"]         = "batch_count"

        await message.reply_text(
            f"\u2705 **Starting point set!** (Message `{start_msg_id}`)\n\n"
            f"\ud83d\udce5 **How many posts to download from here?** (1\u2013{BATCH_MAX}):",
            reply_markup=kb_cancel())
        return

    # ── BATCH Step 3: user sends count ──
    if user_states.get(uid, {}).get("step") == "batch_count":
        try:
            count = int(text)
        except ValueError:
            await message.reply_text("\u274c Please enter a number:", reply_markup=kb_cancel())
            return
        if count < 1 or count > BATCH_MAX:
            await message.reply_text(f"\u274c Must be between 1 and {BATCH_MAX}:", reply_markup=kb_cancel())
            return

        state    = user_states[uid]
        pi       = state["pi"]
        is_priv  = pi.get("is_private", False)
        chat_id  = pi.get("chat_id") or pi.get("username")
        topic_id = pi.get("topic_id")
        is_topic = pi.get("is_topic", False)

        logger.info(f"BATCH COUNT: chat={chat_id} topic={topic_id} is_topic={is_topic} count={count}")

        uc = await get_uc(uid)
        if is_priv and not uc:
            user_states.pop(uid, None)
            await message.reply_text(
                "\ud83d\udd10 **Login required for private groups!**",
                reply_markup=InlineKeyboardMarkup([
                    [InlineKeyboardButton("\ud83d\udd10 Login", callback_data="btn_login")],
                    [InlineKeyboardButton("\ud83d\udd19 Back",  callback_data="main_menu")]]))
            return

        dl    = uc or bot
        title = "Unknown"

        if is_priv:
            ok, title, err = await check_access(uc, chat_id)
            if not ok:
                if err:
                    await bot.send_message(uid, err)
                from config import waiting_invite
                waiting_invite[uid] = {"client": uc, "link": state.get("link", ""), "chat": chat_id}
                await bot.send_message(uid, "\ud83d\udca1 Send an invite link to join, or /cancel to abort.")
                user_states.pop(uid, None)
                return
            dl = uc
        else:
            obj = await safe_get_chat(dl, chat_id)
            if not obj and uc:
                obj = await safe_get_chat(uc, chat_id)
                if obj:
                    dl = uc
            if obj:
                title = getattr(obj, "title", str(chat_id))
            else:
                user_states.pop(uid, None)
                await message.reply_text("\u274c Cannot access this chat!", reply_markup=kb_main())
                return

        un = getattr(message.from_user, "username", None)
        fn = getattr(message.from_user, "first_name", "?")
        start_msg_id = state.get("start_msg_id")

        # Save all params needed to start the batch after forward-choice
        user_states[uid] = {
            "step":         "batch_forward_choice",
            "pi":           pi,
            "chat_id":      chat_id,
            "count":        count,
            "mode":         "topic" if (is_topic and topic_id) else "serial",
            "title":        title,
            "dl":           dl,
            "un":           un,
            "fn":           fn,
            "start_msg_id": start_msg_id,
        }

        mode_label = "Topic-wise" if (is_topic and topic_id) else "Serial-wise"
        topic_note = f"\n🔍 Topic ID: `{topic_id}`" if (is_topic and topic_id) else ""
        start_note = f"\n📌 Starting from message `{start_msg_id}`" if start_msg_id else ""
        await message.reply_text(
            f"✅ **Ready to download {count} posts!**\n"
            f"📺 {title}\n"
            f"📊 Mode: {mode_label}"
            f"{topic_note}{start_note}\n\n"
            f"📤 **Where should the fetched content go?**\n\n"
            f"• **Forward to my Channel/Group** — Bot will send all fetched files "
            f"into a Telegram channel/group of your choice.\n"
            f"  _(You must add this bot as **Admin** in that channel/group first with permission to post messages.)_\n\n"
            f"• **No, send to me** — Bot will send all fetched files directly to you (default).",
            reply_markup=kb_forward_choice())
        return
        # NOTE: run_batch is now started from callbacks_main.py based on the user's choice.

    # ══════════════════════════════════════════════
    # DIRECT LINK HANDLER (no batch state active)
    # ══════════════════════════════════════════════
    links = [l.strip() for l in text.split("\n") if "t.me/" in l or "telegram.me/" in l]
    if not links:
        await message.reply_text(
            "\u274c No valid links found.\nSend a t.me/... link or use \ud83d\udce6 Batch!",
            reply_markup=kb_main())
        return
    if len(links) > 10:
        await message.reply_text(
            "\u274c Max 10 links at once.\nFor more, use \ud83d\udce6 Batch!",
            reply_markup=kb_main())
        return

    # Single link handling
    if len(links) == 1:
        pi = extract_post_info(links[0])
        if pi:
            # Run forum detection for 2-part URLs
            uc = await get_uc(uid)
            detect_client = uc if uc else (bot if not pi.get("is_private") else None)
            if detect_client and pi.get("needs_forum_check"):
                pi = await detect_and_fix_forum(detect_client, pi)

            is_3part = pi.get("is_3part", False)

            if is_3part:
                # 3-part URL (t.me/c/CHAT/TOPIC/MSG): download that specific message
                logger.info(f"SINGLE DOWNLOAD 3-part: msg={pi['message_id']} topic={pi.get('topic_id')} {pi}")
                await process_links(message, links)
                return

            if pi.get("is_topic") and pi.get("topic_id"):
                # 2-part URL detected as forum topic -> auto-batch
                # Ask for starting point first, then count
                logger.info(f"AUTO-BATCH for topic link: {pi}")
                tid = pi.get("topic_id")
                user_states[uid] = {
                    "step": "batch_start_link",
                    "pi": pi,
                    "link": links[0],
                }
                await message.reply_text(
                    f"\ud83d\udd0d **Topic Link Detected!**\n"
                    f"\u2705 **Forum topic detected!** (Topic ID: `{tid}`)\n\n"
                    f"\ud83d\udccc **Send the link of the specific post from which you want to start downloading.**\n"
                    f"_(Send any message link from inside this topic)_",
                    reply_markup=kb_cancel())
                return

            # Not a topic -> download as single message
            logger.info(f"SINGLE DOWNLOAD non-topic: msg={pi['message_id']} {pi}")

    # Regular (non-topic) links -> single download
    await process_links(message, links)


# ═════════════════════════════════════════════
# SINGLE LINK DOWNLOAD
# ═════════════════════════════════════════════
async def process_links(message, links):
    uid   = message.from_user.id
    uname = getattr(message.from_user, "username", None)
    fname = getattr(message.from_user, "first_name", "?")
    rem   = await get_rem(uid)

    if rem < len(links):
        await message.reply_text(
            f"\u26a0\ufe0f Need {len(links)} credits, have {rem}.\n\nGet more credits:",
            reply_markup=kb_no_credits())
        return

    dl_cancel[uid] = False
    ckb    = kb_cancel(uid)
    status = await message.reply_text(f"\u23f3 Processing {len(links)} link(s)...", reply_markup=ckb)
    uc     = await get_uc(uid)
    ok_n   = fail_n = 0
    bot_ids = []

    for idx, link in enumerate(links):
        if dl_cancel.get(uid, False):
            break
        try:
            await status.edit_text(f"\ud83d\udd25 {idx+1}/{len(links)} \u2705{ok_n} \u274c{fail_n}", reply_markup=ckb)
        except Exception:
            pass

        pi = extract_post_info(link)
        if not pi:
            fail_n += 1
            continue

        detect_client = uc if uc else (bot if not pi.get("is_private") else None)
        if detect_client and pi.get("needs_forum_check"):
            pi = await detect_and_fix_forum(detect_client, pi)

        logger.info(f"SINGLE LINK final: {pi}")

        is_priv = pi.get("is_private", False)
        chat_id = pi.get("chat_id") or pi.get("username")
        title   = "?"

        if is_priv:
            if not uc:
                await bot.send_message(uid, "\ud83d\udd10 Login required for private groups!",
                    reply_markup=InlineKeyboardMarkup(
                        [[InlineKeyboardButton("\ud83d\udd10 Login", callback_data="btn_login")]]))
                fail_n += 1
                continue
            ok, title, err = await check_access(uc, chat_id)
            if not ok:
                if err:
                    await bot.send_message(uid, err)
                from config import waiting_invite
                waiting_invite[uid] = {"client": uc, "link": link, "chat": chat_id}
                await bot.send_message(uid, "\ud83d\udca1 Send an invite link or /cancel")
                fail_n += 1
                continue
            dl = uc
        else:
            dl  = uc or bot
            obj = await safe_get_chat(dl, chat_id)
            if not obj and uc and dl != uc:
                obj = await safe_get_chat(uc, chat_id)
                if obj:
                    dl = uc
            if obj:
                title = getattr(obj, "title", str(chat_id))
            else:
                fail_n += 1
                continue

        if dl_cancel.get(uid, False):
            break

        msg = await fetch_one(dl, chat_id, pi["message_id"])
        if not msg:
            fail_n += 1
            await bot.send_message(uid, f"\u274c Message not found: {link}")
            continue

        try:
            ok, mids = await send_content(dl, uid, chat_id, msg, link, ckb, title, uname, fname)
            if ok and mids:
                bot_ids.extend(mids)
                ok_n += 1
                await use_credit(uid)
                info_msg = await bot.send_message(uid, f"\u2705 [Source]({link})\n\u23f0 Auto-delete in 30 min",
                    disable_web_page_preview=True)
                bot_ids.append(info_msg.id)
            elif not dl_cancel.get(uid, False):
                fail_n += 1
                await bot.send_message(uid, f"\u274c Failed: {link}")
        except Exception as e:
            logger.error(f"Single DL error: {e}")
            fail_n += 1

    dl_cancel.pop(uid, None)
    if bot_ids and has_db():
        await db.downloaded_content.insert_one({
            "user_id":      uid,
            "bot_message_ids": bot_ids,
            "timestamp":    datetime.now(timezone.utc).isoformat(),
        })
    ra = await get_rem(uid)
    try:
        await status.edit_text(
            f"\u2705 Done! {ok_n}/{len(links)} \u274c{fail_n} \ud83d\udd25{ra} credits left",
            reply_markup=kb_main())
    except Exception:
        pass
