"""
MESSAGE HANDLER - Admin setup flows, payment flows, support, login, broadcast
Also imports and triggers link_handler for batch/link flows.
"""

import re
import asyncio
import time
import logging
from datetime import datetime, timezone

from pyrogram import Client, filters
from pyrogram.types import Message, InlineKeyboardMarkup, InlineKeyboardButton
from pyrogram.errors import (
    SessionPasswordNeeded, PhoneCodeInvalid, PhoneCodeExpired,
    PasswordHashInvalid, FloodWait,
)

from config import (
    bot, ADMIN_ID, API_ID, API_HASH,
    user_states, user_clients, waiting_invite,
    has_db, users_col, tickets_col, SESSION_DIR,
)
from keyboards import kb_main, kb_cancel, kb_admin, kb_no_credits
from database import (
    get_user, update_user, enforce_fj, is_admin,
    set_fj, set_upi_qr, set_credit_price, set_crypto_qr, set_monthly_price,
    get_upi_qr, get_crypto_qr,
    save_payment_order, get_payment_order, update_payment_status,
)
from keyboards import kb_payment_confirm
from helpers import extract_invite_link, extract_channel_username

logger = logging.getLogger(__name__)


@bot.on_message(filters.private & ~filters.command([
    "start", "help", "status", "referral", "login", "logout", "cancel",
    "mychannels", "batch", "broadcast", "admin", "credit", "reduce",
    "replyuser", "setjoin", "removejoin"]))
async def on_msg(_, message: Message):
    uid  = message.from_user.id
    text = (message.text or "").strip()
    if not await enforce_fj(uid):
        return

    # ── Admin: add admin flow (ONLY main admin) ──
    if user_states.get(uid, {}).get("step") == "add_admin":
        from database import add_admin
        if uid != ADMIN_ID:
            user_states.pop(uid, None)
            return
        try:
            new_admin_id = int(text.strip())
        except (ValueError, TypeError):
            await message.reply_text(
                "\u274c Invalid User ID. Send a number like `123456789`",
                reply_markup=InlineKeyboardMarkup(
                    [[InlineKeyboardButton("\u274c Cancel", callback_data="adm_manage_admins")]]))  
            return
        added = await add_admin(new_admin_id)
        user_states.pop(uid, None)
        if added:
            await message.reply_text(
                f"\u2705 **Admin Added!**\n\n"
                f"\ud83d\udc64 User `{new_admin_id}` is now an admin.\n"
                f"They have full bot management powers.",
                reply_markup=kb_admin())
            try:
                await bot.send_message(new_admin_id,
                    "\ud83d\udc51 **You are now a Bot Admin!**\n\n"
                    "You have full management powers.\n"
                    "Use /admin to open the admin panel.")
            except Exception:
                pass
        else:
            await message.reply_text(
                f"\u26a0\ufe0f User `{new_admin_id}` is already an admin.",
                reply_markup=kb_admin())
        return

    # ── Admin: remove admin flow (ONLY main admin) ──
    if user_states.get(uid, {}).get("step") == "remove_admin":
        from database import remove_admin
        if uid != ADMIN_ID:
            user_states.pop(uid, None)
            return
        try:
            rm_admin_id = int(text.strip())
        except (ValueError, TypeError):
            await message.reply_text(
                "\u274c Invalid User ID. Send a number like `123456789`",
                reply_markup=InlineKeyboardMarkup(
                    [[InlineKeyboardButton("\u274c Cancel", callback_data="adm_manage_admins")]]))  
            return
        if rm_admin_id == ADMIN_ID:
            await message.reply_text(
                "\u274c Cannot remove the **Owner**! Only added admins can be removed.",
                reply_markup=kb_admin())
            user_states.pop(uid, None)
            return
        removed = await remove_admin(rm_admin_id)
        user_states.pop(uid, None)
        if removed:
            await message.reply_text(
                f"\u2705 **Admin Removed!**\n\n"
                f"\ud83d\udc64 User `{rm_admin_id}` is no longer an admin.",
                reply_markup=kb_admin())
            try:
                await bot.send_message(rm_admin_id,
                    "\u26a0\ufe0f Your admin access has been revoked.")
            except Exception:
                pass
        else:
            await message.reply_text(
                f"\u274c User `{rm_admin_id}` is not in the admin list.",
                reply_markup=kb_admin())
        return

    # ── User: Forward-target channel/group (after picking "Forward to my Channel/Group") ──
    if user_states.get(uid, {}).get("step") == "batch_forward_channel":
        st = user_states.get(uid, {})
        target_id = None
        target_title = None
        try:
            if message.forward_from_chat:
                target_id = message.forward_from_chat.id
                target_title = message.forward_from_chat.title or str(target_id)
            elif text.startswith("@"):
                ch = await bot.get_chat(text[1:])
                target_id = ch.id
                target_title = ch.title or text
            elif text.startswith("-100") or (text.lstrip("-").isdigit()):
                ch = await bot.get_chat(int(text))
                target_id = ch.id
                target_title = ch.title or str(target_id)
            elif "t.me/" in text:
                # Public @username inside a link
                from helpers import extract_channel_username
                un = extract_channel_username(text)
                if un:
                    ch = await bot.get_chat(un)
                    target_id = ch.id
                    target_title = ch.title or un
            if target_id is None:
                await message.reply_text(
                    "❌ Invalid. Send `@username`, `-100XXXXXXXXXX`, or **forward a message** from the channel/group.",
                    reply_markup=kb_cancel())
                return
        except Exception as e:
            await message.reply_text(
                f"❌ Could not access that chat: `{e}`\n\n"
                "Make sure the bot is added as **Admin** there and the ID/username is correct.",
                reply_markup=kb_cancel())
            return

        # Verify bot is admin (or at least can post) in the target chat
        try:
            me = await bot.get_me()
            member = await bot.get_chat_member(target_id, me.id)
            from pyrogram.enums import ChatMemberStatus
            if member.status not in (ChatMemberStatus.OWNER, ChatMemberStatus.ADMINISTRATOR):
                await message.reply_text(
                    f"❌ **Bot is not admin** in **{target_title}**!\n\n"
                    f"Please add me as an Admin (with *Post Messages* permission) and try again.",
                    reply_markup=kb_cancel())
                return
        except Exception as e:
            await message.reply_text(
                f"❌ **Cannot verify admin status** in **{target_title or target_id}**.\n"
                f"`{e}`\n\n"
                f"Add the bot to that channel/group as Admin first, then retry.",
                reply_markup=kb_cancel())
            return

        # All good — start the batch with forwarding enabled
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

        # Notify the target chat that the bot will start forwarding
        try:
            await bot.send_message(target_id,
                f"🤖 **Bot is now forwarding fetched content here.**\n"
                f"Requested by user `{uid}` — {count} posts from **{title}**.")
        except Exception:
            pass

        await message.reply_text(
            f"✅ **Forwarding enabled!**\n"
            f"📤 Target: **{target_title}** (`{target_id}`)\n\n"
            f"⏳ Starting download of **{count}** posts from **{title}**...\n"
            f"_All fetched files will be sent to the target chat above._",
            reply_markup=kb_cancel(uid))

        from sender import run_batch
        asyncio.create_task(
            run_batch(uid, un, fn, pi, chat_id, count, mode, title, dl,
                      start_from_msg_id=start_msg_id, forward_to=target_id))
        return

    # ── Admin: force_join setup ──
    if await is_admin(uid) and user_states.get(uid, {}).get("step") == "set_fj":
        try:
            cid = cu = None
            if message.forward_from_chat:
                cid = message.forward_from_chat.id
                cu  = message.forward_from_chat.username
            elif text.startswith("@"):
                cu  = text[1:]
                ch  = await bot.get_chat(cu)
                cid = ch.id
            elif text.startswith("-100"):
                cid = int(text)
                ch  = await bot.get_chat(cid)
                cu  = ch.username
            else:
                await message.reply_text("\u274c Invalid. Send @username, -100ID, or forward.")
                return
            await set_fj(cid, cu)
            user_states.pop(uid, None)
            await message.reply_text(f"\u2705 Force join set: {'@' + cu if cu else cid}", reply_markup=kb_admin())
        except Exception as e:
            await message.reply_text(f"\u274c Error: {e}")
        return

    # ── Admin: Set UPI QR code ──
    if await is_admin(uid) and user_states.get(uid, {}).get("step") == "set_upi_qr":
        if message.photo:
            file_id = message.photo.file_id
            await set_upi_qr(file_id)
            user_states.pop(uid, None)
            await message.reply_text("\u2705 UPI QR code saved successfully!", reply_markup=kb_admin())
        else:
            await message.reply_text("\u274c Please send a photo/image of the QR code.")
        return

    # ── Admin: Set credit price ──
    if await is_admin(uid) and user_states.get(uid, {}).get("step") == "set_credit_price":
        try:
            price = float(text)
            if price <= 0:
                raise ValueError
            await set_credit_price(price)
            user_states.pop(uid, None)
            await message.reply_text(f"\u2705 Price set: \u20b9{price}/credit", reply_markup=kb_admin())
        except (ValueError, TypeError):
            await message.reply_text("\u274c Invalid price. Send a positive number (e.g. 1, 1.5, 2).")
        return

    # ── Admin: Set crypto QR code ──
    if await is_admin(uid) and user_states.get(uid, {}).get("step") == "set_crypto_qr":
        if message.photo:
            file_id = message.photo.file_id
            await set_crypto_qr(file_id)
            user_states[uid] = {"step": "set_crypto_addr", "qr_file_id": file_id}
            await message.reply_text("\u2705 Crypto QR saved!\n\nNow send your **wallet address** (text):")
        else:
            await message.reply_text("\u274c Please send a photo/image of the crypto QR code.")
        return

    # ── Admin: Set crypto wallet address ──
    if await is_admin(uid) and user_states.get(uid, {}).get("step") == "set_crypto_addr":
        if text:
            qr_fid = user_states[uid].get("qr_file_id")
            await set_crypto_qr(qr_fid, text)
            user_states.pop(uid, None)
            await message.reply_text(
                f"\u2705 Crypto setup complete!\n\n\ud83d\uddbc QR: Saved\n\ud83d\udccb Address: `{text}`",
                reply_markup=kb_admin())
        else:
            await message.reply_text("\u274c Please send the wallet address as text.")
        return

    # ── Admin: Add credits - step 1: get user ID ──
    if await is_admin(uid) and user_states.get(uid, {}).get("step") == "add_credits_uid":
        try:
            target_id = int(text.strip())
        except (ValueError, TypeError):
            await message.reply_text(
                "❌ Invalid User ID. Send a number like `123456789`",
                reply_markup=InlineKeyboardMarkup(
                    [[InlineKeyboardButton("❌ Cancel", callback_data="btn_admin")]]))
            return
        if not has_db():
            await message.reply_text("❌ Database not connected.", reply_markup=kb_admin())
            user_states.pop(uid, None)
            return
        u = await users_col.find_one({"user_id": target_id})
        if not u:
            await message.reply_text(
                f"❌ User `{target_id}` not found in database.",
                reply_markup=kb_admin())
            user_states.pop(uid, None)
            return
        current = u.get("bonus_downloads", 0)
        user_states[uid] = {"step": "add_credits_amt", "target_id": target_id, "current": current}
        uname = u.get("username") or "N/A"
        await message.reply_text(
            f"💎 **Add Credits**\n\n"
            f"👤 User: `{target_id}` | @{uname}\n"
            f"💰 Current bonus credits: **{current}**\n\n"
            f"How many credits to add? (1–10000):",
            reply_markup=InlineKeyboardMarkup(
                [[InlineKeyboardButton("❌ Cancel", callback_data="btn_admin")]]))
        return

    # ── Admin: Add credits - step 2: get amount ──
    if await is_admin(uid) and user_states.get(uid, {}).get("step") == "add_credits_amt":
        try:
            amt = int(text.strip())
            if amt < 1 or amt > 10000:
                raise ValueError
        except (ValueError, TypeError):
            await message.reply_text("❌ Enter a number between 1 and 10000.")
            return
        target_id = user_states[uid]["target_id"]
        current   = user_states[uid]["current"]
        await users_col.update_one({"user_id": target_id}, {"$inc": {"bonus_downloads": amt}})
        user_states.pop(uid, None)
        await message.reply_text(
            f"✅ **Credits Added!**\n\n"
            f"👤 User: `{target_id}`\n"
            f"➕ Added: **{amt}** credits\n"
            f"💰 New total bonus: **{current + amt}**",
            reply_markup=kb_admin())
        try:
            await bot.send_message(target_id, f"🎁 **+{amt} credits** added to your account by admin!")
        except Exception:
            pass
        return

    # ── Admin: Reduce credits - step 1: get user ID ──
    if await is_admin(uid) and user_states.get(uid, {}).get("step") == "reduce_credits_uid":
        try:
            target_id = int(text.strip())
        except (ValueError, TypeError):
            await message.reply_text(
                "❌ Invalid User ID. Send a number like `123456789`",
                reply_markup=InlineKeyboardMarkup(
                    [[InlineKeyboardButton("❌ Cancel", callback_data="btn_admin")]]))
            return
        if not has_db():
            await message.reply_text("❌ Database not connected.", reply_markup=kb_admin())
            user_states.pop(uid, None)
            return
        u = await users_col.find_one({"user_id": target_id})
        if not u:
            await message.reply_text(
                f"❌ User `{target_id}` not found in database.",
                reply_markup=kb_admin())
            user_states.pop(uid, None)
            return
        current = u.get("bonus_downloads", 0)
        user_states[uid] = {"step": "reduce_credits_amt", "target_id": target_id, "current": current}
        uname = u.get("username") or "N/A"
        await message.reply_text(
            f"➖ **Reduce Credits**\n\n"
            f"👤 User: `{target_id}` | @{uname}\n"
            f"💰 Current bonus credits: **{current}**\n\n"
            f"How many credits to deduct? (1–10000):",
            reply_markup=InlineKeyboardMarkup(
                [[InlineKeyboardButton("❌ Cancel", callback_data="btn_admin")]]))
        return

    # ── Admin: Reduce credits - step 2: get amount ──
    if await is_admin(uid) and user_states.get(uid, {}).get("step") == "reduce_credits_amt":
        try:
            amt = int(text.strip())
            if amt < 1 or amt > 10000:
                raise ValueError
        except (ValueError, TypeError):
            await message.reply_text("❌ Enter a number between 1 and 10000.")
            return
        target_id = user_states[uid]["target_id"]
        current   = user_states[uid]["current"]
        new_val   = max(0, current - amt)
        deducted  = current - new_val
        await users_col.update_one({"user_id": target_id}, {"$set": {"bonus_downloads": new_val}})
        user_states.pop(uid, None)
        await message.reply_text(
            f"✅ **Credits Reduced!**\n\n"
            f"👤 User: `{target_id}`\n"
            f"➖ Deducted: **{deducted}** credits\n"
            f"💰 Remaining bonus: **{new_val}**",
            reply_markup=kb_admin())
        try:
            await bot.send_message(target_id, f"⚠️ **{deducted} credits** have been deducted from your account by admin.")
        except Exception:
            pass
        return

    # ── Admin: Set monthly price ──
    if await is_admin(uid) and user_states.get(uid, {}).get("step") == "set_monthly_price":
        try:
            price = float(text)
            if price <= 0:
                raise ValueError
            await set_monthly_price(int(price))
            user_states.pop(uid, None)
            await message.reply_text(f"\u2705 Monthly plan price set: \u20b9{int(price)}/month", reply_markup=kb_admin())
        except (ValueError, TypeError):
            await message.reply_text("\u274c Invalid price. Send a positive number (e.g. 99, 199, 299).")
        return

    # ── User: Custom credits amount (UPI) ──
    if user_states.get(uid, {}).get("step") == "pay_custom":
        try:
            credits = int(text)
            if credits <= 0:
                raise ValueError
            price = user_states[uid].get("price", 1.0)
            amount = int(price * credits)
            if amount <= 0:
                amount = 1
            order_id = await save_payment_order(uid, credits, amount)
            user_states.pop(uid, None)
            if not order_id:
                await message.reply_text("\u274c Error creating order.", reply_markup=kb_main())
                return
            qr = await get_upi_qr()
            if not qr:
                await message.reply_text("\u274c UPI not configured. Use referral.", reply_markup=kb_no_credits())
                return
            await bot.send_photo(uid, qr,
                caption=f"\ud83d\udcb3 **Pay \u20b9{amount} for {credits} Credits**\n\n"
                        f"\ud83d\udccc Order: `{order_id}`\n\n"
                        f"1\ufe0f\u20e3 Scan the QR code above\n"
                        f"2\ufe0f\u20e3 Pay exactly **\u20b9{amount}**\n"
                        f"3\ufe0f\u20e3 Click the button below after payment",
                reply_markup=kb_payment_confirm(order_id))
        except (ValueError, TypeError):
            await message.reply_text("\u274c Invalid number. Send a positive whole number (e.g. 25).")
        return

    # ── User: Payment screenshot ──
    if user_states.get(uid, {}).get("step") == "pay_screenshot":
        order_id = user_states[uid].get("order_id")
        if not message.photo:
            await message.reply_text("\ud83d\udcf8 Please send a **screenshot** (photo) of your payment.")
            return
        order = await get_payment_order(order_id)
        if not order or order.get("status") != "pending":
            await message.reply_text("\u274c Order expired or already submitted.", reply_markup=kb_main())
            user_states.pop(uid, None)
            return
        await update_payment_status(order_id, "screenshot_sent")
        user_states.pop(uid, None)
        un = message.from_user.username or "N/A"
        fn = message.from_user.first_name or "?"
        otype = order.get("order_type", "credits")
        type_label = "\ud83d\udcc5 Monthly Plan" if otype == "monthly" else ("\ud83e\ude99 Crypto" if otype == "crypto" else "\ud83d\udcb3 UPI Credits")
        credits_line = "\ud83d\udcc5 Plan: **Monthly (Unlimited)**" if otype == "monthly" else f"\ud83d\udc8e Credits: **{order['credits']}**"
        from keyboards import kb_payment_approve
        await bot.send_photo(ADMIN_ID, message.photo.file_id,
            caption=f"\ud83d\udcb3 **Payment Verification Request**\n\n"
                    f"\ud83c\udff7 Type: {type_label}\n"
                    f"\ud83d\udc64 User: [{fn}](tg://user?id={uid}) | @{un}\n"
                    f"\ud83c\udd94 ID: `{uid}`\n"
                    f"\ud83d\udccc Order: `{order_id}`\n"
                    f"{credits_line}\n"
                    f"\ud83d\udcb0 Amount: **\u20b9{order['amount']}**\n\n"
                    f"\u2b07\ufe0f Payment screenshot above",
            reply_markup=kb_payment_approve(order_id))
        await message.reply_text(
            f"\u2705 **Screenshot Submitted!**\n\n"
            f"\ud83d\udccc Order: `{order_id}`\n"
            f"\u23f3 Waiting for admin verification...\n\n"
            f"You'll be notified once your payment is approved.",
            reply_markup=kb_main())
        return

    # ── Support ticket ──
    if user_states.get(uid, {}).get("step") == "support":
        un = message.from_user.username or "N/A"
        fn = message.from_user.first_name or "?"
        ticket_text = text or (message.caption or "").strip() or "(no text)"

        if has_db():
            try:
                await tickets_col.insert_one({
                    "user_id": uid, "username": un, "first_name": fn,
                    "message": ticket_text,
                    "created_at": datetime.now(timezone.utc).isoformat()
                })
            except Exception as db_err:
                logger.error(f"[support] DB insert failed: {db_err}")

        if not ADMIN_ID or ADMIN_ID == 0:
            logger.warning("[support] ADMIN_ID is 0 or not set")
            await message.reply_text("\u2705 Support ticket saved!", reply_markup=kb_main())
            user_states.pop(uid, None)
            return

        separator = '\u2501' * 20
        support_msg = (
            f"\ud83c\udfab SUPPORT TICKET\n"
            f"{separator}\n"
            f"\ud83d\udc64 Name : {fn}\n"
            f"\ud83d\udcf1 User : @{un}\n"
            f"\ud83c\udd94 ID   : {uid}\n"
            f"{separator}\n"
            f"\ud83d\udcdd Message:\n{ticket_text}\n"
            f"{separator}\n"
            f"Reply: /replyuser {uid} <your reply>"
        )

        delivered = False
        if message.photo:
            try:
                await bot.send_photo(ADMIN_ID, message.photo.file_id, caption=support_msg)
                delivered = True
            except Exception as e:
                logger.error(f"[support] Photo send failed: {e}")

        if not delivered:
            try:
                await bot.send_message(ADMIN_ID, support_msg)
                delivered = True
            except Exception as e:
                logger.error(f"[support] Text send failed: {e}")

        if not delivered:
            try:
                await bot.send_message(ADMIN_ID, f"Support from {uid} (@{un}): {ticket_text[:500]}")
                delivered = True
            except Exception as e:
                logger.error(f"[support] ALL delivery attempts failed: {e}")

        await message.reply_text("\u2705 Support ticket sent!", reply_markup=kb_main())
        user_states.pop(uid, None)
        return

    # ── Admin broadcast text+image ──
    if await is_admin(uid) and user_states.get(uid, {}).get("step") == "bc_text":
        st = await message.reply_text("\ud83d\udce4 Broadcasting...")
        if has_db():
            ul = await users_col.find({}).to_list(None)
            s  = f = 0
            has_photo = message.photo is not None
            photo_id  = message.photo.file_id if has_photo else None
            caption   = (message.caption or "").strip() if has_photo else ""
            for u in ul:
                try:
                    if has_photo:
                        await bot.send_photo(u["user_id"], photo_id,
                            caption=f"\ud83d\udce2 {caption}" if caption else None)
                    else:
                        await bot.send_message(u["user_id"], f"\ud83d\udce2 {text}")
                    s += 1
                    await asyncio.sleep(0.05)
                except Exception:
                    f += 1
            await st.edit_text(f"\u2705 Sent: {s} | \u274c Failed: {f}")
        user_states.pop(uid, None)
        return

    # ── LOGIN FLOW ──
    st = user_states.get(uid, {})

    if st.get("step") == "phone":
        phone = text
        if not re.match(r'^\+\d{10,15}$', phone):
            await message.reply_text("\u274c Invalid format. Use: `+1234567890`", reply_markup=kb_cancel())
            return
        try:
            tc = Client(f"temp_{uid}_{int(time.time())}", api_id=API_ID, api_hash=API_HASH,
                        phone_number=phone, workdir=str(SESSION_DIR))
            await tc.connect()
            code = await tc.send_code(phone)
            user_states[uid].update({"phone": phone, "hash": code.phone_code_hash, "tc": tc, "step": "code"})
            await message.reply_text("\ud83d\udce9 OTP sent! Enter with spaces: `1 2 3 4 5`", reply_markup=kb_cancel())
        except FloodWait as e:
            await message.reply_text(f"\u23f3 Too many attempts. Wait {e.value}s.")
            user_states.pop(uid, None)
        except Exception as e:
            await message.reply_text(f"\u274c Error: {e}")
            user_states.pop(uid, None)
        return

    if st.get("step") == "code":
        code = text.replace("-", "").replace(" ", "")
        try:
            tc = st["tc"]
            try:
                await tc.sign_in(st["phone"], st["hash"], code)
            except SessionPasswordNeeded:
                user_states[uid]["step"] = "2fa"
                await message.reply_text("\ud83d\udd10 2FA required. Enter your password:", reply_markup=kb_cancel())
                return
            ss = await tc.export_session_string()
            me = await tc.get_me()
            await tc.disconnect()
            user_clients.pop(uid, None)
            await update_user(uid, {"session_string": ss, "phone": st["phone"], "logged_in": True})
            user_states.pop(uid, None)
            await message.reply_text(
                f"\u2705 **Logged in!**\n{me.first_name} @{me.username} {me.phone_number}",
                reply_markup=kb_main())
        except PhoneCodeInvalid:
            await message.reply_text("\u274c Invalid code. Use spaces: `1 2 3 4 5`")
        except PhoneCodeExpired:
            await message.reply_text("\u274c Code expired. Start over.", reply_markup=kb_main())
            user_states.pop(uid, None)
        except Exception as e:
            await message.reply_text(f"\u274c Error: {e}")
            user_states.pop(uid, None)
        return

    if st.get("step") == "2fa":
        try:
            tc = st["tc"]
            await tc.check_password(text)
            ss = await tc.export_session_string()
            me = await tc.get_me()
            await tc.disconnect()
            user_clients.pop(uid, None)
            await update_user(uid, {"session_string": ss, "phone": st["phone"], "logged_in": True})
            user_states.pop(uid, None)
            await message.reply_text(
                f"\u2705 **Logged in!**\n{me.first_name} {me.phone_number}",
                reply_markup=kb_main())
        except PasswordHashInvalid:
            await message.reply_text("\u274c Wrong password. Try again:")
        except Exception as e:
            await message.reply_text(f"\u274c Error: {e}")
            user_states.pop(uid, None)
        return

    # ── Invite link handling ──
    if uid in waiting_invite:
        inv = extract_invite_link(text)
        if not inv:
            un = extract_channel_username(text)
            if un:
                inv = f"@{un}"
        if not inv:
            await message.reply_text("\u274c Not an invite link. Try again or /cancel.")
            return
        from downloader import join_inv
        sm  = await message.reply_text("\ud83d\udd04 Joining...")
        ok, res = await join_inv(waiting_invite[uid]["client"], inv, uid)
        if ok:
            await sm.edit_text(f"\u2705 Joined: {res}\nNow resend your original link!")
            waiting_invite.pop(uid, None)
        else:
            await sm.edit_text(f"\u274c Failed: {res}")
        return

    # ── LINK / BATCH FLOW (handled in link_handler.py) ──
    from link_handler import handle_links_and_batch
    await handle_links_and_batch(message, uid, text)
