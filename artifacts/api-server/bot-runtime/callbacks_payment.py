"""
CALLBACKS PAYMENT - UPI, Crypto, Monthly plan, credit tiers, approve/reject
"""

import logging

from pyrogram.types import InlineKeyboardMarkup, InlineKeyboardButton

from config import bot, ADMIN_ID, user_states
from keyboards import (
    kb_main, kb_no_credits, kb_credit_tiers, kb_crypto_tiers,
    kb_monthly_method, kb_payment_confirm, kb_payment_approve,
)
from database import (
    get_user, update_user, get_upi_qr, get_credit_price,
    get_crypto_qr, get_monthly_price,
    has_monthly_plan, activate_monthly_plan,
    save_payment_order, get_payment_order, update_payment_status,
    is_admin,
)

logger = logging.getLogger(__name__)

# INR to USD conversion rate
INR_TO_USD = 86.0


def inr_to_dollar(inr_amount):
    """Convert INR to USD string."""
    usd = inr_amount / INR_TO_USD
    if usd < 1:
        return f"${usd:.2f}"
    return f"${usd:.1f}" if usd != int(usd) else f"${int(usd)}"


async def handle_payment_callbacks(q, uid, d):
    """
    Handle payment-related callbacks. Returns True if handled, False otherwise.
    """

    # ══════════════════════════════════════════
    # MONTHLY PLAN - show UPI / Crypto choice
    # ══════════════════════════════════════════
    if d == "pay_monthly":
        await q.answer()
        if await has_monthly_plan(uid):
            u = await get_user(uid)
            exp = u.get("monthly_plan_expiry", "N/A")
            try:
                await q.message.edit_text(
                    f"\u2705 You already have an active monthly plan!\n\n"
                    f"Expires: `{str(exp)[:10]}`\n\n"
                    f"Enjoy unlimited downloads!",
                    reply_markup=kb_main())
            except Exception:
                pass
            return True
        mp = await get_monthly_price()
        try:
            await q.message.edit_text(
                f"\ud83d\udcc5 **Monthly Plan \u2014 Unlimited Downloads**\n\n"
                f"\ud83d\udcb0 Price: \u20b9{int(mp)}/month\n\n"
                f"Choose your payment method:",
                reply_markup=kb_monthly_method())
        except Exception:
            pass
        return True

    # Monthly UPI
    if d == "monthly_upi":
        await q.answer()
        qr = await get_upi_qr()
        if not qr:
            try:
                await q.message.edit_text("\u274c UPI not set up yet. Contact admin.", reply_markup=kb_no_credits())
            except Exception:
                pass
            return True
        mp = await get_monthly_price()
        order_id = await save_payment_order(uid, 0, int(mp), order_type="monthly")
        if not order_id:
            try:
                await q.message.edit_text("\u274c Error creating order.", reply_markup=kb_main())
            except Exception:
                pass
            return True
        try:
            await q.message.delete()
        except Exception:
            pass
        try:
            await bot.send_photo(uid, qr,
                caption=f"\ud83d\udcc5 **Monthly Plan \u2014 \u20b9{int(mp)}**\n\n"
                        f"\ud83d\udccc Order: `{order_id}`\n\n"
                        f"1\ufe0f\u20e3 Scan the QR code above\n"
                        f"2\ufe0f\u20e3 Pay exactly **\u20b9{int(mp)}**\n"
                        f"3\ufe0f\u20e3 Click the button below after payment",
                reply_markup=kb_payment_confirm(order_id))
        except Exception as e:
            logger.error(f"[pay] Monthly UPI QR error: {e}")
            await bot.send_message(uid, "\u274c Error loading QR.", reply_markup=kb_main())
        return True

    # Monthly Crypto
    if d == "monthly_crypto":
        await q.answer()
        cqr, caddr = await get_crypto_qr()
        if not cqr:
            try:
                await q.message.edit_text("\u274c Crypto payment not set up yet. Contact admin.", reply_markup=kb_no_credits())
            except Exception:
                pass
            return True
        mp = await get_monthly_price()
        usd_str = inr_to_dollar(int(mp))
        order_id = await save_payment_order(uid, 0, int(mp), order_type="monthly_crypto")
        if not order_id:
            try:
                await q.message.edit_text("\u274c Error creating order.", reply_markup=kb_main())
            except Exception:
                pass
            return True
        try:
            await q.message.delete()
        except Exception:
            pass
        addr_line = f"\n\n\ud83d\udccb **Wallet Address:**\n`{caddr}`" if caddr else ""
        try:
            await bot.send_photo(uid, cqr,
                caption=f"\ud83d\udcc5 **Monthly Plan \u2014 {usd_str} (\u20b9{int(mp)})**\n\n"
                        f"\ud83d\udccc Order: `{order_id}`{addr_line}\n\n"
                        f"1\ufe0f\u20e3 Scan the QR code or copy address\n"
                        f"2\ufe0f\u20e3 Send exactly **{usd_str}** in crypto\n"
                        f"3\ufe0f\u20e3 Click the button below after payment",
                reply_markup=kb_payment_confirm(order_id))
        except Exception as e:
            logger.error(f"[pay] Monthly Crypto QR error: {e}")
            await bot.send_message(uid, "\u274c Error loading QR.", reply_markup=kb_main())
        return True

    # ══════════════════════════════════════════
    # CRYPTO PAYMENT - $ based tiers
    # ══════════════════════════════════════════
    if d == "pay_crypto":
        await q.answer()
        cqr, caddr = await get_crypto_qr()
        if not cqr:
            try:
                await q.message.edit_text(
                    "\u274c Crypto payment is not available yet.\nAdmin hasn't set it up.\n\nPlease use other payment methods!",
                    reply_markup=kb_no_credits())
            except Exception:
                pass
            return True
        price = await get_credit_price()
        try:
            await q.message.edit_text(
                f"\ud83e\ude99 **Buy Credits via Crypto**\n\n"
                f"\ud83d\udcb2 Price: {inr_to_dollar(price)}/credit\n"
                f"\ud83d\udcb1 Rate: \u20b986 = $1\n\n"
                f"Select a package:",
                reply_markup=kb_crypto_tiers(price))
        except Exception:
            pass
        return True

    # Crypto tier selection (dynamic based on INR price)
    if d.startswith("cpay_"):
        tier_key = d.replace("cpay_", "")
        try:
            credits = int(tier_key)
        except (ValueError, TypeError):
            return False
        if credits <= 0:
            return False
        await q.answer()
        price = await get_credit_price()
        inr_amount = int(price * credits)
        usd_str = inr_to_dollar(inr_amount)
        order_id = await save_payment_order(uid, credits, inr_amount, order_type="crypto")
        if not order_id:
            try:
                await q.message.edit_text("\u274c Error creating order.", reply_markup=kb_main())
            except Exception:
                pass
            return True
        cqr, caddr = await get_crypto_qr()
        if not cqr:
            try:
                await q.message.edit_text("\u274c Crypto not configured.", reply_markup=kb_no_credits())
            except Exception:
                pass
            return True
        try:
            await q.message.delete()
        except Exception:
            pass
        addr_line = f"\n\n\ud83d\udccb **Wallet Address:**\n`{caddr}`" if caddr else ""
        try:
            await bot.send_photo(uid, cqr,
                caption=f"\ud83e\ude99 **Crypto Payment**\n\n"
                        f"\ud83d\udcb0 Amount: **{usd_str}** (\u20b9{inr_amount})\n"
                        f"\ud83d\udc8e Credits: **{credits}**\n"
                        f"\ud83d\udccc Order: `{order_id}`{addr_line}\n\n"
                        f"1\ufe0f\u20e3 Scan the QR code or copy address\n"
                        f"2\ufe0f\u20e3 Send exactly **{usd_str}** in crypto\n"
                        f"3\ufe0f\u20e3 Click **Confirm Payment** after sending",
                reply_markup=kb_payment_confirm(order_id))
        except Exception as e:
            logger.error(f"[pay] Crypto QR error: {e}")
            await bot.send_message(uid, "\u274c Error loading QR.", reply_markup=kb_main())
        return True

    # ══════════════════════════════════════════
    # UPI PAYMENT
    # ══════════════════════════════════════════
    if d == "pay_upi":
        await q.answer()
        qr = await get_upi_qr()
        if not qr:
            try:
                await q.message.edit_text(
                    "\u274c UPI payment is not available yet.\nAdmin hasn't set up the QR code.\n\nPlease use referral to earn credits!",
                    reply_markup=kb_no_credits())
            except Exception:
                pass
            return True
        price = await get_credit_price()
        try:
            await q.message.edit_text(
                f"\ud83d\udcb3 **Buy Credits via UPI**\n\n"
                f"\ud83d\udcb2 Price: \u20b9{price}/credit\n\n"
                f"Select a package:",
                reply_markup=kb_credit_tiers(price))
        except Exception:
            pass
        return True

    # UPI tier selection
    if d in ("pay_10", "pay_50", "pay_100"):
        await q.answer()
        credits = int(d.split("_")[1])
        price = await get_credit_price()
        amount = int(price * credits)
        order_id = await save_payment_order(uid, credits, amount)
        if not order_id:
            try:
                await q.message.edit_text("\u274c Error creating order.", reply_markup=kb_main())
            except Exception:
                pass
            return True
        qr = await get_upi_qr()
        try:
            await q.message.delete()
        except Exception:
            pass
        try:
            await bot.send_photo(uid, qr,
                caption=f"\ud83d\udcb3 **Pay \u20b9{amount} for {credits} Credits**\n\n"
                        f"\ud83d\udccc Order: `{order_id}`\n\n"
                        f"1\ufe0f\u20e3 Scan the QR code above\n"
                        f"2\ufe0f\u20e3 Pay exactly **\u20b9{amount}**\n"
                        f"3\ufe0f\u20e3 Click the button below after payment",
                reply_markup=kb_payment_confirm(order_id))
        except Exception as e:
            logger.error(f"[pay] QR send error: {e}")
            await bot.send_message(uid, "\u274c Error loading QR code.", reply_markup=kb_main())
        return True

    # UPI custom
    if d == "pay_custom":
        await q.answer()
        price = await get_credit_price()
        user_states[uid] = {"step": "pay_custom", "price": price}
        try:
            await q.message.edit_text(
                f"\ud83d\udd22 **Custom Credits**\n\n"
                f"\ud83d\udcb2 Price: \u20b9{price}/credit\n\n"
                f"Enter the number of credits you want to buy:",
                reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("\u274c Cancel", callback_data="main_menu")]]))
        except Exception:
            pass
        return True

    # ══════════════════════════════════════════
    # PAYMENT CONFIRMATION (user clicks confirm)
    # ══════════════════════════════════════════
    if d.startswith("pay_confirm_"):
        order_id = d.replace("pay_confirm_", "")
        order = await get_payment_order(order_id)
        if not order or order.get("status") != "pending":
            await q.answer("\u274c Invalid or expired order.", show_alert=True)
            return True
        if order.get("user_id") != uid:
            await q.answer("\u274c This is not your order.", show_alert=True)
            return True
        await q.answer()
        user_states[uid] = {"step": "pay_screenshot", "order_id": order_id}
        try:
            await q.message.edit_reply_markup(reply_markup=None)
        except Exception:
            pass
        otype = order.get("order_type", "credits")
        if otype in ("crypto", "monthly_crypto"):
            credits_line = f"\ud83d\udc8e Credits: {order['credits']}" if order.get('credits') else "\ud83d\udcc5 Plan: Monthly (Unlimited)"
            await bot.send_message(uid,
                f"\ud83d\udcf8 **Send Payment Screenshot**\n\n"
                f"\ud83d\udccc Order: `{order_id}`\n"
                f"{credits_line}\n\n"
                f"Send the screenshot of your crypto transaction now:",
                reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("\u274c Cancel", callback_data="main_menu")]]))
        else:
            await bot.send_message(uid,
                f"\ud83d\udcf8 **Send Payment Screenshot**\n\n"
                f"\ud83d\udccc Order: `{order_id}`\n"
                f"\ud83d\udcb0 Amount: \u20b9{order['amount']} for {order['credits']} credits\n\n"
                f"Send the screenshot of your payment now:",
                reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("\u274c Cancel", callback_data="main_menu")]]))
        return True

    # ══════════════════════════════════════════
    # ADMIN: Approve payment
    # ══════════════════════════════════════════
    if d.startswith("pay_approve_") and await is_admin(uid):
        order_id = d.replace("pay_approve_", "")
        order = await get_payment_order(order_id)
        if not order:
            await q.answer("\u274c Order not found.", show_alert=True)
            return True
        if order.get("status") != "screenshot_sent":
            await q.answer("\u274c Already processed.", show_alert=True)
            return True
        await update_payment_status(order_id, "approved")
        target_uid = order["user_id"]
        credits = order.get("credits", 0)
        otype = order.get("order_type", "credits")
        if otype in ("monthly", "monthly_crypto"):
            await activate_monthly_plan(target_uid)
            approve_txt = f"Monthly plan activated for user `{target_uid}`"
            user_msg = (f"\u2705 **Payment Approved!**\n\n"
                        f"\ud83d\udcc5 **Monthly Plan Activated!**\n"
                        f"\ud83d\udd25 Unlimited downloads for 30 days!\n"
                        f"\ud83d\udccc Order: `{order_id}`")
        else:
            u = await get_user(target_uid)
            new_bonus = u.get("bonus_downloads", 0) + credits
            await update_user(target_uid, {"bonus_downloads": new_bonus})
            approve_txt = f"{credits} credits added to user `{target_uid}`"
            user_msg = (f"\u2705 **Payment Approved!**\n\n"
                        f"\ud83d\udc8e **{credits} credits** added to your account!\n"
                        f"\ud83d\udccc Order: `{order_id}`\n\n"
                        f"\ud83d\udd25 Enjoy downloading!")
        try:
            await q.message.edit_reply_markup(reply_markup=None)
        except Exception:
            pass
        cap = q.message.caption or q.message.text or ""
        try:
            await q.message.edit_caption(caption=cap + f"\n\n\u2705 **APPROVED** \u2014 {approve_txt}")
        except Exception:
            try:
                await q.message.edit_text(cap + f"\n\n\u2705 **APPROVED** \u2014 {approve_txt}")
            except Exception:
                pass
        try:
            await bot.send_message(target_uid, user_msg, reply_markup=kb_main())
        except Exception as e:
            logger.error(f"[pay] Notify user {target_uid} approve fail: {e}")
        await q.answer("\u2705 Approved!", show_alert=True)
        return True

    # ══════════════════════════════════════════
    # ADMIN: Reject payment
    # ══════════════════════════════════════════
    if d.startswith("pay_reject_") and await is_admin(uid):
        order_id = d.replace("pay_reject_", "")
        order = await get_payment_order(order_id)
        if not order:
            await q.answer("\u274c Order not found.", show_alert=True)
            return True
        if order.get("status") != "screenshot_sent":
            await q.answer("\u274c Already processed.", show_alert=True)
            return True
        await update_payment_status(order_id, "rejected")
        target_uid = order["user_id"]
        try:
            await q.message.edit_reply_markup(reply_markup=None)
        except Exception:
            pass
        cap = q.message.caption or q.message.text or ""
        try:
            await q.message.edit_caption(caption=cap + f"\n\n\u274c **REJECTED** \u2014 Order `{order_id}`")
        except Exception:
            try:
                await q.message.edit_text(cap + f"\n\n\u274c **REJECTED** \u2014 Order `{order_id}`")
            except Exception:
                pass
        try:
            await bot.send_message(target_uid,
                f"\u274c **Payment Rejected**\n\n"
                f"\ud83d\udccc Order: `{order_id}`\n\n"
                f"Your payment could not be verified. "
                f"Please try again with the correct amount.",
                reply_markup=kb_no_credits())
        except Exception as e:
            logger.error(f"[pay] Notify user {target_uid} reject fail: {e}")
        await q.answer("\u274c Rejected", show_alert=True)
        return True

    return False
