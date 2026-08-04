"""
KEYBOARDS - All InlineKeyboardMarkup functions
"""

from pyrogram.types import InlineKeyboardMarkup, InlineKeyboardButton


def kb_main():
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("📊 Status",   callback_data="btn_status"),
         InlineKeyboardButton("❓ Help",      callback_data="btn_help")],
        [InlineKeyboardButton("🔐 Login",    callback_data="btn_login"),
         InlineKeyboardButton("🔓 Logout",   callback_data="btn_logout")],
        [InlineKeyboardButton("📦 Batch",    callback_data="btn_batch"),
         InlineKeyboardButton("🎁 Referral", callback_data="btn_referral")],
        [InlineKeyboardButton("💰 Deposit",  callback_data="btn_deposit")],
        [InlineKeyboardButton("📺 Channels", callback_data="btn_mychannels"),
         InlineKeyboardButton("💬 Support",  callback_data="btn_support")]])


def kb_admin():
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("📢 Broadcast", callback_data="adm_bc"),
         InlineKeyboardButton("📊 Stats",     callback_data="adm_stats")],
        [InlineKeyboardButton("💎 Credits",   callback_data="adm_credits"),
         InlineKeyboardButton("🔒 ForceJoin", callback_data="adm_fj")],
        [InlineKeyboardButton("🖼 Set UPI QR",   callback_data="adm_set_qr"),
         InlineKeyboardButton("💲 Price/Credit", callback_data="adm_set_price")],
        [InlineKeyboardButton("🪙 Set Crypto QR", callback_data="adm_set_crypto"),
         InlineKeyboardButton("📅 Monthly Price", callback_data="adm_monthly_price")],
        [InlineKeyboardButton("🗑 Delete UPI QR",    callback_data="adm_del_upi_qr"),
         InlineKeyboardButton("🗑 Delete Crypto QR", callback_data="adm_del_crypto_qr")],
        [InlineKeyboardButton("➖ Reduce Credits", callback_data="adm_reduce")],
        [InlineKeyboardButton("👁 Shadow",    callback_data="adm_shadow")],
        [InlineKeyboardButton("👑 Manage Admins", callback_data="adm_manage_admins")],
        [InlineKeyboardButton("🔙 Menu",      callback_data="main_menu")]])


def kb_no_credits():
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("🎁 Referral", callback_data="btn_referral")],
        [InlineKeyboardButton("💳 UPI Payment", callback_data="pay_upi"),
         InlineKeyboardButton("🪙 Crypto", callback_data="pay_crypto")],
        [InlineKeyboardButton("📅 Monthly Plan (Unlimited)", callback_data="pay_monthly")],
        [InlineKeyboardButton("🔙 Menu", callback_data="main_menu")]])


def kb_credit_tiers(price):
    return InlineKeyboardMarkup([
        [InlineKeyboardButton(f"10 Credits = ₹{int(price * 10)}", callback_data="pay_10")],
        [InlineKeyboardButton(f"50 Credits = ₹{int(price * 50)}", callback_data="pay_50")],
        [InlineKeyboardButton(f"100 Credits = ₹{int(price * 100)}", callback_data="pay_100")],
        [InlineKeyboardButton("🔢 Custom Amount", callback_data="pay_custom")],
        [InlineKeyboardButton("🔙 Back", callback_data="main_menu")]])


def kb_crypto_tiers(price_inr):
    """Dollar-based crypto credit tiers. Auto-converts INR price to USD at ₹86 = $1."""
    inr_to_usd = 86.0
    tiers = [
        (10,  price_inr * 10),
        (50,  price_inr * 50),
        (100, price_inr * 100),
        (200, price_inr * 200),
        (500, price_inr * 500),
    ]
    buttons = []
    for credits, inr_amount in tiers:
        usd = inr_amount / inr_to_usd
        if usd < 1:
            usd_str = f"${usd:.2f}"
        else:
            usd_str = f"${usd:.1f}" if usd != int(usd) else f"${int(usd)}"
        buttons.append([InlineKeyboardButton(
            f"{credits} Credits = {usd_str}",
            callback_data=f"cpay_{credits}")])
    buttons.append([InlineKeyboardButton("🔙 Back", callback_data="main_menu")])
    return InlineKeyboardMarkup(buttons)


def kb_monthly_method():
    """Choose UPI or Crypto for monthly plan."""
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("💳 Pay via UPI",    callback_data="monthly_upi")],
        [InlineKeyboardButton("🪙 Pay via Crypto", callback_data="monthly_crypto")],
        [InlineKeyboardButton("🔙 Back", callback_data="main_menu")]])


def kb_payment_confirm(order_id):
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("✅ Confirm Payment", callback_data=f"pay_confirm_{order_id}")],
        [InlineKeyboardButton("❌ Cancel", callback_data="main_menu")]])


def kb_payment_approve(order_id):
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("✅ Approve", callback_data=f"pay_approve_{order_id}"),
         InlineKeyboardButton("❌ Reject",  callback_data=f"pay_reject_{order_id}")]])


def kb_back():
    return InlineKeyboardMarkup([[InlineKeyboardButton("🔙 Menu", callback_data="main_menu")]])


def kb_cancel(uid=None):
    return InlineKeyboardMarkup([[InlineKeyboardButton("❌ Cancel",
        callback_data=f"cancel_dl_{uid}" if uid else "btn_cancel")]])


def kb_forward_choice():
    """Ask user: forward fetched content to their channel/group, or receive normally."""
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("📤 Forward to my Channel / Group", callback_data="fwd_yes")],
        [InlineKeyboardButton("❌ No, send to me",                  callback_data="fwd_no")],
        [InlineKeyboardButton("🔙 Cancel",                          callback_data="btn_cancel")],
    ])


def kb_batch_mode(topic_id=None):
    """Mode selection keyboard - always shown for topic links."""
    rows = [
        [InlineKeyboardButton("1️⃣ Topic-wise (only this topic)",  callback_data="bm_topic")],
        [InlineKeyboardButton("2️⃣ Serial-wise (all messages)",    callback_data="bm_serial")],
        [InlineKeyboardButton("❌ Cancel",                          callback_data="btn_cancel")],
    ]
    return InlineKeyboardMarkup(rows)
