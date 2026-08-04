"""
CALLBACKS ADMIN - Admin panel callbacks (stats, credits, force join, UPI QR, crypto, pricing, shadow, manage admins)
"""

import logging

from pyrogram.types import InlineKeyboardMarkup, InlineKeyboardButton

from config import bot, ADMIN_ID, user_states, user_clients, has_db, users_col
from keyboards import kb_admin
from database import (
    get_fj, get_upi_qr, get_credit_price, get_crypto_qr, get_monthly_price,
    is_admin, get_all_admins, delete_upi_qr, delete_crypto_qr,
)

logger = logging.getLogger(__name__)


async def handle_admin_callbacks(q, uid, d):
    """
    Handle admin panel callbacks. Returns True if handled, False otherwise.
    """
    if not await is_admin(uid):
        return False

    # Broadcast
    if d == "adm_bc":
        user_states[uid] = {"step": "bc_text"}
        try:
            await q.message.edit_text(
                "📢 Send the message to broadcast:\n\n"
                "📝 Text only: just type your message\n"
                "🖼\ufe0f Text + Image: send a photo with caption")
        except Exception:
            pass
        return True

    # Stats
    if d == "adm_stats":
        t  = await users_col.count_documents({}) if has_db() else 0
        a  = await users_col.aggregate(
            [{"$group": {"_id": None, "t": {"$sum": "$total_downloads"}}}]
        ).to_list(1) if has_db() else []
        dl = a[0]["t"] if a else 0
        try:
            await q.message.edit_text(
                f"📊 Users: {t}\n🔥 Downloads: {dl}\n🔗 Active sessions: {len(user_clients)}",
                reply_markup=kb_admin())
        except Exception:
            pass
        return True

    # Credits - interactive flow
    if d == "adm_credits":
        user_states[uid] = {"step": "add_credits_uid"}
        try:
            await q.message.edit_text(
                "💎 **Add Credits**\n\n"
                "Send the **User ID** of the user you want to credit:\n\n"
                "_(e.g. `123456789`)_",
                reply_markup=InlineKeyboardMarkup(
                    [[InlineKeyboardButton("❌ Cancel", callback_data="btn_admin")]]))
        except Exception:
            pass
        return True

    # Reduce credits - interactive flow
    if d == "adm_reduce":
        user_states[uid] = {"step": "reduce_credits_uid"}
        try:
            await q.message.edit_text(
                "➖ **Reduce Credits**\n\n"
                "Send the **User ID** of the user whose credits you want to reduce:\n\n"
                "_(e.g. `123456789`)_",
                reply_markup=InlineKeyboardMarkup(
                    [[InlineKeyboardButton("❌ Cancel", callback_data="btn_admin")]]))
        except Exception:
            pass
        return True

    # Force Join info
    if d == "adm_fj":
        fid, fu = await get_fj()
        t = f"🔒 Force join: {'@' + fu if fu else str(fid)}" if fid else "🔓 Force join: Off"
        try:
            await q.message.edit_text(f"{t}\n`/setjoin` `/removejoin`", reply_markup=kb_admin())
        except Exception:
            pass
        return True

    # Shadow info
    if d == "adm_shadow":
        try:
            await q.message.edit_text("👁 Shadow monitoring: ✅ ON", reply_markup=kb_admin())
        except Exception:
            pass
        return True

    # Set UPI QR
    if d == "adm_set_qr":
        user_states[uid] = {"step": "set_upi_qr"}
        qr = await get_upi_qr()
        status_txt = "✅ QR is set" if qr else "❌ No QR set"
        try:
            await q.message.edit_text(
                f"🖼 **UPI QR Code**\n\nCurrent: {status_txt}\n\n📤 Send your UPI QR code image now:",
                reply_markup=InlineKeyboardMarkup(
                    [[InlineKeyboardButton("🔙 Cancel", callback_data="btn_admin")]]))
        except Exception:
            pass
        return True

    # Set Credit Price
    if d == "adm_set_price":
        user_states[uid] = {"step": "set_credit_price"}
        price = await get_credit_price()
        try:
            await q.message.edit_text(
                f"💲 **Price Per Credit**\n\nCurrent: ₹{price}/credit\n\n"
                f"Send the new price per credit (number):",
                reply_markup=InlineKeyboardMarkup(
                    [[InlineKeyboardButton("🔙 Cancel", callback_data="btn_admin")]]))
        except Exception:
            pass
        return True

    # Set Crypto QR
    if d == "adm_set_crypto":
        user_states[uid] = {"step": "set_crypto_qr"}
        cqr, caddr = await get_crypto_qr()
        st = f"✅ Set (Address: `{caddr}`)" if cqr else "❌ Not set"
        try:
            await q.message.edit_text(
                f"🪙 **Crypto Payment Setup**\n\nCurrent: {st}\n\n"
                f"📤 Send your crypto QR code image.\n"
                f"Then send your wallet address in the next message.",
                reply_markup=InlineKeyboardMarkup(
                    [[InlineKeyboardButton("🔙 Cancel", callback_data="btn_admin")]]))
        except Exception:
            pass
        return True

    # Delete UPI QR
    if d == "adm_del_upi_qr":
        qr = await get_upi_qr()
        if not qr:
            await q.answer("❌ No UPI QR set to delete.", show_alert=True)
            return True
        await delete_upi_qr()
        await q.answer("✅ UPI QR deleted!", show_alert=True)
        try:
            await q.message.edit_text(
                "✅ **UPI QR Code Deleted!**\n\n"
                "Users will not be able to pay via UPI until you set a new QR.",
                reply_markup=kb_admin())
        except Exception:
            pass
        return True

    # Delete Crypto QR
    if d == "adm_del_crypto_qr":
        cqr, _ = await get_crypto_qr()
        if not cqr:
            await q.answer("❌ No Crypto QR set to delete.", show_alert=True)
            return True
        await delete_crypto_qr()
        await q.answer("✅ Crypto QR deleted!", show_alert=True)
        try:
            await q.message.edit_text(
                "✅ **Crypto QR & Address Deleted!**\n\n"
                "Users will not be able to pay via Crypto until you set a new QR.",
                reply_markup=kb_admin())
        except Exception:
            pass
        return True

    # Set Monthly Price
    if d == "adm_monthly_price":
        user_states[uid] = {"step": "set_monthly_price"}
        mp = await get_monthly_price()
        try:
            await q.message.edit_text(
                f"📅 **Monthly Plan Price**\n\nCurrent: ₹{mp}/month (unlimited)\n\n"
                f"Send the new monthly plan price (number):",
                reply_markup=InlineKeyboardMarkup(
                    [[InlineKeyboardButton("🔙 Cancel", callback_data="btn_admin")]]))
        except Exception:
            pass
        return True

    # Manage Admins - all admins can view, only main admin can add/remove
    if d == "adm_manage_admins":
        admins = await get_all_admins()
        admin_list = ""
        for i, aid in enumerate(admins, 1):
            role = "Owner" if aid == ADMIN_ID else "Admin"
            admin_list += f"  {i}. `{aid}` - {role}\n"

        # Only main admin sees Add/Remove buttons
        if uid == ADMIN_ID:
            buttons = [
                [InlineKeyboardButton("Add Admin", callback_data="adm_add_admin"),
                 InlineKeyboardButton("Remove Admin", callback_data="adm_rm_admin")],
                [InlineKeyboardButton("Back", callback_data="btn_admin")]]
        else:
            buttons = [[InlineKeyboardButton("Back", callback_data="btn_admin")]]

        try:
            await q.message.edit_text(
                f"**Manage Admins**\n\n"
                f"**Current Admins:**\n{admin_list}\n"
                + ("You can add or remove admins." if uid == ADMIN_ID else "Only the main admin can add/remove admins."),
                reply_markup=InlineKeyboardMarkup(buttons))
        except Exception:
            pass
        return True

    # Add Admin - ONLY main admin (ADMIN_ID) can add new admins
    if d == "adm_add_admin":
        if uid != ADMIN_ID:
            await q.answer("Only the main admin can add new admins!", show_alert=True)
            return True
        user_states[uid] = {"step": "add_admin"}
        try:
            await q.message.edit_text(
                "➕ **Add New Admin**\n\n"
                "Send the **User ID** (number) of the person you want to make admin.\n\n"
                "💡 User can find their ID by sending /status to this bot.",
                reply_markup=InlineKeyboardMarkup(
                    [[InlineKeyboardButton("❌ Cancel", callback_data="adm_manage_admins")]]))
        except Exception:
            pass
        return True

    # Remove Admin - ONLY main admin (ADMIN_ID) can remove admins
    if d == "adm_rm_admin":
        if uid != ADMIN_ID:
            await q.answer("Only the main admin can remove admins!", show_alert=True)
            return True
        user_states[uid] = {"step": "remove_admin"}
        admins = await get_all_admins()
        added_admins = [a for a in admins if a != ADMIN_ID]
        if not added_admins:
            try:
                await q.message.edit_text(
                    "⚠️ No added admins to remove.\nOnly the owner exists.",
                    reply_markup=InlineKeyboardMarkup(
                        [[InlineKeyboardButton("🔙 Back", callback_data="adm_manage_admins")]]))
            except Exception:
                pass
            user_states.pop(uid, None)
            return True
        admin_list = ""
        for aid in added_admins:
            admin_list += f"  \u2022 `{aid}`\n"
        try:
            await q.message.edit_text(
                f"➖ **Remove Admin**\n\n"
                f"**Added Admins:**\n{admin_list}\n"
                f"Send the **User ID** to remove:",
                reply_markup=InlineKeyboardMarkup(
                    [[InlineKeyboardButton("❌ Cancel", callback_data="adm_manage_admins")]]))
        except Exception:
            pass
        return True

    return False
