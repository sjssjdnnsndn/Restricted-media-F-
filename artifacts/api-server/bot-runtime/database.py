"""
DATABASE - User CRUD, payments, force join, settings, credits, referrals
"""

import logging
from datetime import datetime, timedelta, timezone

from pyrogram import Client

from config import (
    API_ID, API_HASH, DAILY_LIMIT, REFERRAL_BONUS, RESET_HOURS,
    has_db, users_col, settings_col, db,
    user_clients, SESSION_DIR,
)

logger = logging.getLogger(__name__)


# ═══════════════════════════════════════════
# MULTI-ADMIN SYSTEM
# ═══════════════════════════════════════════
async def is_admin(uid):
    """Check if user is the owner (ADMIN_ID) or a added admin."""
    from config import ADMIN_ID
    if uid == ADMIN_ID:
        return True
    if not has_db():
        return False
    try:
        doc = await settings_col.find_one({"setting": "admins"})
        if doc and uid in doc.get("admin_ids", []):
            return True
    except Exception:
        pass
    return False


async def add_admin(uid):
    """Add a user as admin. Returns True if added, False if already admin."""
    if not has_db():
        return False
    doc = await settings_col.find_one({"setting": "admins"})
    current = doc.get("admin_ids", []) if doc else []
    if uid in current:
        return False
    current.append(uid)
    await settings_col.update_one(
        {"setting": "admins"},
        {"$set": {"setting": "admins", "admin_ids": current}},
        upsert=True)
    return True


async def remove_admin(uid):
    """Remove a user from admins. Returns True if removed, False if not found."""
    if not has_db():
        return False
    doc = await settings_col.find_one({"setting": "admins"})
    if not doc:
        return False
    current = doc.get("admin_ids", [])
    if uid not in current:
        return False
    current.remove(uid)
    await settings_col.update_one(
        {"setting": "admins"},
        {"$set": {"admin_ids": current}})
    return True


async def get_all_admins():
    """Get list of all admin IDs (including owner)."""
    from config import ADMIN_ID
    admins = [ADMIN_ID]
    if not has_db():
        return admins
    try:
        doc = await settings_col.find_one({"setting": "admins"})
        if doc:
            for aid in doc.get("admin_ids", []):
                if aid not in admins:
                    admins.append(aid)
    except Exception:
        pass
    return admins


# ═══════════════════════════════════════════
# FORCE JOIN
# ═══════════════════════════════════════════
async def get_fj():
    if not has_db():
        return None, None
    try:
        s = await settings_col.find_one({"setting": "force_join"})
        if s:
            return s.get("channel_id"), s.get("channel_username")
    except Exception:
        pass
    return None, None


async def set_fj(cid, cu=None):
    if not has_db():
        return
    await settings_col.update_one(
        {"setting": "force_join"},
        {"$set": {"setting": "force_join", "channel_id": cid, "channel_username": cu}},
        upsert=True)


async def rm_fj():
    if has_db():
        await settings_col.delete_one({"setting": "force_join"})


# ═══════════════════════════════════════════
# UPI / CRYPTO / PRICING SETTINGS
# ═══════════════════════════════════════════
async def get_upi_qr():
    if not has_db():
        return None
    try:
        s = await settings_col.find_one({"setting": "upi_qr"})
        if s:
            return s.get("file_id")
    except Exception:
        pass
    return None


async def set_upi_qr(file_id):
    if not has_db():
        return
    await settings_col.update_one(
        {"setting": "upi_qr"},
        {"$set": {"setting": "upi_qr", "file_id": file_id}},
        upsert=True)


async def get_credit_price():
    if not has_db():
        return 1.0
    try:
        s = await settings_col.find_one({"setting": "credit_price"})
        if s:
            return s.get("price", 1.0)
    except Exception:
        pass
    return 1.0


async def set_credit_price(price):
    if not has_db():
        return
    await settings_col.update_one(
        {"setting": "credit_price"},
        {"$set": {"setting": "credit_price", "price": price}},
        upsert=True)


async def get_monthly_price():
    if not has_db():
        return 99
    try:
        s = await settings_col.find_one({"setting": "monthly_price"})
        if s:
            return s.get("price", 99)
    except Exception:
        pass
    return 99


async def set_monthly_price(price):
    if not has_db():
        return
    await settings_col.update_one(
        {"setting": "monthly_price"},
        {"$set": {"setting": "monthly_price", "price": price}},
        upsert=True)


async def get_crypto_qr():
    if not has_db():
        return None, None
    try:
        s = await settings_col.find_one({"setting": "crypto_qr"})
        if s:
            return s.get("file_id"), s.get("address")
    except Exception:
        pass
    return None, None


async def set_crypto_qr(file_id, address=None):
    if not has_db():
        return
    data = {"setting": "crypto_qr", "file_id": file_id}
    if address:
        data["address"] = address
    await settings_col.update_one(
        {"setting": "crypto_qr"},
        {"$set": data},
        upsert=True)


async def delete_upi_qr():
    """Delete the UPI QR code from settings."""
    if has_db():
        await settings_col.delete_one({"setting": "upi_qr"})


async def delete_crypto_qr():
    """Delete the Crypto QR code and address from settings."""
    if has_db():
        await settings_col.delete_one({"setting": "crypto_qr"})


# ═══════════════════════════════════════════
# MONTHLY PLAN
# ═══════════════════════════════════════════
async def has_monthly_plan(uid):
    u = await get_user(uid)
    exp = u.get("monthly_plan_expiry")
    if not exp:
        return False
    try:
        expiry = datetime.fromisoformat(str(exp))
        if expiry.tzinfo is None:
            expiry = expiry.replace(tzinfo=timezone.utc)
        return datetime.now(timezone.utc) < expiry
    except Exception:
        return False


async def activate_monthly_plan(uid):
    expiry = (datetime.now(timezone.utc) + timedelta(days=30)).isoformat()
    await update_user(uid, {"monthly_plan_expiry": expiry})


# ═══════════════════════════════════════════
# PAYMENT ORDERS
# ═══════════════════════════════════════════
async def save_payment_order(uid, credits, amount, status="pending", order_type="credits"):
    if not has_db():
        return None
    import uuid
    order_id = str(uuid.uuid4())[:8]
    await db.payment_orders.insert_one({
        "order_id": order_id,
        "user_id": uid,
        "credits": credits,
        "amount": amount,
        "order_type": order_type,
        "status": status,
        "created_at": datetime.now(timezone.utc).isoformat()
    })
    return order_id


async def get_payment_order(order_id):
    if not has_db():
        return None
    return await db.payment_orders.find_one({"order_id": order_id})


async def update_payment_status(order_id, status):
    if not has_db():
        return
    await db.payment_orders.update_one(
        {"order_id": order_id},
        {"$set": {"status": status, "updated_at": datetime.now(timezone.utc).isoformat()}})


# ═══════════════════════════════════════════
# USER DATA (CRUD + Credits)
# ═══════════════════════════════════════════
async def get_user(uid):
    default = {
        "user_id": uid, "downloads_today": 0,
        "last_reset": datetime.now(timezone.utc).isoformat(),
        "total_downloads": 0, "referrals": [], "referred_by": None,
        "bonus_downloads": 0, "session_string": None, "phone": None, "logged_in": False,
    }
    if not has_db():
        return default
    u = await users_col.find_one({"user_id": uid})
    if not u:
        await users_col.insert_one(default.copy())
        u = default
    if u.get("session_string") and u.get("logged_in") and uid not in user_clients:
        try:
            c = Client(f"user_{uid}", api_id=API_ID, api_hash=API_HASH,
                       session_string=u["session_string"], workdir=str(SESSION_DIR))
            await c.start()
            user_clients[uid] = c
            me = await c.get_me()
            logger.info(f"Session restored {uid} -> {me.first_name}")
        except Exception as e:
            logger.error(f"Restore fail {uid}: {e}")
            await users_col.update_one({"user_id": uid}, {"$set": {"logged_in": False}})
    return u


async def update_user(uid, data):
    if has_db():
        await users_col.update_one({"user_id": uid}, {"$set": data})


async def reset_if_needed(uid):
    u = await get_user(uid)
    try:
        last = datetime.fromisoformat(str(u.get("last_reset", "")))
        if last.tzinfo is None:
            last = last.replace(tzinfo=timezone.utc)
    except Exception:
        last = datetime.now(timezone.utc) - timedelta(hours=RESET_HOURS + 1)
    if (datetime.now(timezone.utc) - last).total_seconds() > RESET_HOURS * 3600:
        await update_user(uid, {
            "downloads_today": 0,
            "last_reset": datetime.now(timezone.utc).isoformat()})


async def get_rem(uid):
    if await has_monthly_plan(uid):
        return 999999
    await reset_if_needed(uid)
    u = await get_user(uid)
    return max(0, DAILY_LIMIT + u.get("bonus_downloads", 0) - u.get("downloads_today", 0))


async def use_credit(uid):
    """
    Deducts exactly 1 credit per download.
    Priority: bonus_downloads first, then daily_limit.
    """
    u = await get_user(uid)
    upd = {"total_downloads": u.get("total_downloads", 0) + 1}

    if u.get("bonus_downloads", 0) > 0:
        upd["bonus_downloads"] = u["bonus_downloads"] - 1
        logger.info(f"[credit] uid={uid} used 1 bonus credit, remaining={u['bonus_downloads']-1}")
    else:
        upd["downloads_today"] = u.get("downloads_today", 0) + 1
        logger.info(f"[credit] uid={uid} used 1 daily credit, today={u.get('downloads_today',0)+1}/{DAILY_LIMIT}")

    await update_user(uid, upd)


async def do_referral(ref_id, new_id):
    r = await get_user(ref_id)
    if new_id in r.get("referrals", []):
        return False
    await update_user(ref_id, {
        "referrals": r.get("referrals", []) + [new_id],
        "bonus_downloads": r.get("bonus_downloads", 0) + REFERRAL_BONUS})
    await update_user(new_id, {"referred_by": ref_id})
    return True


# ═══════════════════════════════════════════
# USER CLIENT
# ═══════════════════════════════════════════
async def get_uc(uid):
    if uid in user_clients:
        c = user_clients[uid]
        try:
            if not c.is_connected:
                await c.start()
            return c
        except Exception:
            user_clients.pop(uid, None)
    if not has_db():
        return None
    u = await users_col.find_one({"user_id": uid})
    if not u or not u.get("session_string"):
        return None
    try:
        c = Client(f"user_{uid}", api_id=API_ID, api_hash=API_HASH,
                   session_string=u["session_string"], workdir=str(SESSION_DIR))
        await c.start()
        user_clients[uid] = c
        return c
    except Exception as e:
        logger.error(f"Client fail {uid}: {e}")
        return None


# ═══════════════════════════════════════════
# FORCE JOIN CHECK
# ═══════════════════════════════════════════
from pyrogram.enums import ChatMemberStatus
from pyrogram.types import InlineKeyboardMarkup, InlineKeyboardButton


async def in_ch(uid, cid):
    from config import bot
    try:
        m = await bot.get_chat_member(cid, uid)
        return m.status in (ChatMemberStatus.OWNER, ChatMemberStatus.ADMINISTRATOR, ChatMemberStatus.MEMBER)
    except Exception:
        return False


async def enforce_fj(uid):
    from config import bot
    fid, fu = await get_fj()
    if not fid or await is_admin(uid):
        return True
    if await in_ch(uid, fid):
        return True
    link = f"https://t.me/{fu}" if fu else f"https://t.me/c/{str(fid)[4:]}"
    ch_name = f"@{fu}" if fu else "our channel"
    try:
        await bot.send_message(uid,
            f"╔═══════════════════════╗\n"
            f"   🔐 **ACCESS RESTRICTED**\n"
            f"╚═══════════════════════╝\n\n"
            f"👋 Hey there! To use this bot, you need to\n"
            f"join our official channel first.\n\n"
            f"📢 **Channel:** {ch_name}\n\n"
            f"━━━━━━━━━━━━━━━━━━━━━\n"
            f"📌 **How to get access:**\n"
            f"  1️⃣ Tap **Join Channel** below\n"
            f"  2️⃣ Join the channel\n"
            f"  3️⃣ Come back & tap **✅ Verify**\n"
            f"━━━━━━━━━━━━━━━━━━━━━\n\n"
            f"🚀 Unlock unlimited downloads after joining!",
            reply_markup=InlineKeyboardMarkup([
                [InlineKeyboardButton("📢 Join Channel", url=link)],
                [InlineKeyboardButton("✅ I've Joined \u2014 Verify", callback_data="verify_join")]]))
    except Exception as e:
        logger.warning(f"[enforce_fj] Could not send message to {uid}: {e}")
    return False
