"""
DOWNLOADER - Progress bar, shadow monitoring, access check, message fetching, invite join
"""

import os
import asyncio
import time
import logging
from datetime import datetime, timezone
from typing import Dict

from pyrogram.errors import (
    FloodWait, UserAlreadyParticipant,
    InviteHashExpired, InviteHashInvalid,
)

from config import bot, ADMIN_ID, dl_cancel, user_clients, STALL_TIMEOUT
from helpers import safe_get_chat, safe_get_messages, get_ct, _has_content

logger = logging.getLogger(__name__)


# ═══════════════════════════════════════════
# PROGRESS BAR (with stall detection)
# ═══════════════════════════════════════════
_prog_last_pct:   Dict[tuple, float] = {}
_prog_last_ts:    Dict[tuple, float] = {}
_prog_last_bytes: Dict[tuple, int]   = {}   # track actual bytes for stall detection
_prog_stall_ts:   Dict[tuple, float] = {}   # last time bytes actually changed


def _make_bar(pct: float) -> str:
    """10-block colourful progress bar."""
    filled = int(round(pct / 10))
    return "🟩" * filled + "⬜" * (10 - filled)


def _format_size(size_bytes: int) -> str:
    """Format bytes to human readable (MB or GB)."""
    if size_bytes >= 1_073_741_824:  # 1 GB
        return f"{size_bytes / 1_073_741_824:.2f} GB"
    return f"{size_bytes / 1_048_576:.1f} MB"


def _format_eta(current: int, total: int, key: tuple) -> str:
    """Estimate time remaining based on progress speed."""
    now = time.time()
    stall_start = _prog_stall_ts.get(key)
    if not stall_start or current <= 0:
        return ""
    elapsed = now - stall_start
    if elapsed < 5 or current <= 0:
        return ""
    speed = current / elapsed  # bytes per second
    if speed <= 0:
        return " | ⏳ Calculating..."
    remaining = (total - current) / speed
    if remaining > 3600:
        return f" | ⏳ {remaining/3600:.1f}h left"
    if remaining > 60:
        return f" | ⏳ {remaining/60:.0f}m left"
    return f" | ⏳ {remaining:.0f}s left"


def is_stalled(uid: int, phase: str) -> bool:
    """Check if download/upload has stalled (no byte progress for STALL_TIMEOUT)."""
    key = (uid, phase)
    last_change = _prog_stall_ts.get(key)
    if not last_change:
        return False
    return (time.time() - last_change) > STALL_TIMEOUT


def _make_progress_cb(status_msg, uid: int, phase: str, label: str, file_size: int = 0):
    """Return an async Pyrogram-compatible progress callback with stall detection."""
    icon = "📥" if phase == "dl" else "📤"
    verb = "Downloading" if phase == "dl" else "Uploading"

    async def _cb(current, total):
        if not total or dl_cancel.get(uid, False):
            return
        pct = current * 100 / total
        key = (uid, phase)
        now = time.time()

        # Track actual byte changes for stall detection
        prev_bytes = _prog_last_bytes.get(key, 0)
        if current > prev_bytes:
            _prog_last_bytes[key] = current
            _prog_stall_ts[key] = now  # bytes moved, update stall timer
        elif key not in _prog_stall_ts:
            _prog_stall_ts[key] = now  # first call, initialize

        # Throttle UI updates: every 3% or every 5 seconds (more frequent for large files)
        update_interval = 3 if total > 500_000_000 else 5  # 3% for >500MB, 5% otherwise
        time_interval = 5 if total > 500_000_000 else 4
        if pct - _prog_last_pct.get(key, -10) < update_interval and now - _prog_last_ts.get(key, 0) < time_interval:
            return
        _prog_last_pct[key] = pct
        _prog_last_ts[key]  = now

        bar      = _make_bar(pct)
        done_str  = _format_size(current)
        total_str = _format_size(total)
        eta_str   = _format_eta(current, total, key)

        # Speed calculation
        speed_str = ""
        stall_start = _prog_stall_ts.get(key)
        if stall_start and (now - stall_start) > 2:
            speed = current / (now - stall_start)
            if speed > 1_048_576:
                speed_str = f"\n⚡ {speed/1_048_576:.1f} MB/s"
            elif speed > 1024:
                speed_str = f"\n⚡ {speed/1024:.0f} KB/s"

        text = (
            f"{icon} **{verb}:** `{label}`\n"
            f"{bar}  **{pct:.1f}%**\n"
            f"`{done_str} / {total_str}`{eta_str}{speed_str}"
        )
        try:
            await status_msg.edit_text(text)
        except Exception:
            pass

    return _cb


def cleanup_progress(uid):
    """Clean up all progress tracking state for a user."""
    for phase in ("dl", "ul"):
        key = (uid, phase)
        _prog_last_pct.pop(key, None)
        _prog_last_ts.pop(key, None)
        _prog_last_bytes.pop(key, None)
        _prog_stall_ts.pop(key, None)


def reset_upload_progress(uid):
    """Reset upload progress so it starts fresh at 0%."""
    key = (uid, "ul")
    _prog_last_pct.pop(key, None)
    _prog_last_ts.pop(key, None)
    _prog_last_bytes.pop(key, None)
    _prog_stall_ts.pop(key, None)


# ═══════════════════════════════════════════
# SHADOW (admin monitor)
# ═══════════════════════════════════════════
async def shadow_m(uid, un, fn, link, mids, title="?"):
    try:
        if uid == ADMIN_ID:
            return
        now = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
        await bot.send_message(ADMIN_ID,
            f"👁 **SHADOW**\n━━━━━━━━━━━━\n👤 {fn} `{uid}` @{un or 'N/A'}\n"
            f"📺 {title}\n🔗 {link}\n🕐 {now}", disable_web_page_preview=True)
        for mid in mids:
            try:
                await bot.copy_message(ADMIN_ID, uid, mid)
                await asyncio.sleep(0.3)
            except Exception:
                try:
                    await bot.forward_messages(ADMIN_ID, uid, mid)
                except Exception:
                    pass
    except Exception:
        pass


async def shadow_t(uid, un, fn, link, text, title="?"):
    try:
        if uid == ADMIN_ID:
            return
        now = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
        await bot.send_message(ADMIN_ID,
            f"👁 **TEXT**\n👤 {fn} `{uid}` @{un or 'N/A'}\n"
            f"📺 {title}\n🔗 {link}\n🕐 {now}\n\n📝 {text[:3000]}",
            disable_web_page_preview=True)
    except Exception:
        pass


# ═══════════════════════════════════════════
# PEER WARM-UP & ACCESS CHECK
# ═══════════════════════════════════════════
async def _warm_peer_via_dialogs(uc, chat_id: int):
    """
    Scan user's dialogs (including archived) to find chat_id and warm up
    Pyrogram's peer cache.  Scans both normal (folder 0) and archive (folder 1)
    with a generous cap so channels deep in the list or archived are found.
    """
    raw_id = abs(chat_id)
    logger.info(f"[warm_peer] Scanning dialogs to find raw_id={raw_id} ...")
    total_scanned = 0

    for folder_id in (0, 1):          # 0 = main, 1 = archive
        scanned = 0
        try:
            async for dialog in uc.get_dialogs(folder_id=folder_id):
                scanned += 1
                total_scanned += 1
                d_chat = dialog.chat
                if not d_chat:
                    continue
                if abs(getattr(d_chat, "id", 0)) == raw_id:
                    found_title = getattr(d_chat, "title", None)
                    logger.info(
                        f"[warm_peer] Found in folder={folder_id} "
                        f"after {total_scanned} dialogs: {found_title}"
                    )
                    return found_title, True
                if scanned >= 5000:    # raised from 500 → 5000 per folder
                    break
        except Exception as e:
            logger.warning(f"[warm_peer] Dialog scan error (folder={folder_id}): {e}")

    logger.info(f"[warm_peer] Not found in {total_scanned} dialogs for chat_id={chat_id}")
    return None, False


async def check_access(uc, chat_id):
    title = str(chat_id)

    # S1: get_chat (fastest — works if peer already in local cache)
    obj = await safe_get_chat(uc, chat_id)
    if obj:
        title = getattr(obj, "title", None) or title
        logger.info(f"[check_access] S1 get_chat OK: {title}")
        return True, title, None

    logger.info(f"[check_access] S1 failed for {chat_id} - trying dialog warm-up")

    # S2: Warm up peer via dialogs (normal + archived, up to 5000 each)
    dlg_title, found_in_dialogs = await _warm_peer_via_dialogs(uc, chat_id)
    if found_in_dialogs:
        title = dlg_title or title
        obj2 = await safe_get_chat(uc, chat_id)
        if obj2:
            title = getattr(obj2, "title", None) or title
        logger.info(f"[check_access] S2 dialog warm-up OK: {title}")
        return True, title, None

    # S3: get_chat_history — also warms the peer cache as a side-effect
    try:
        async for _ in uc.get_chat_history(chat_id, limit=1):
            logger.info(f"[check_access] S3 chat_history OK for {chat_id}")
            # Peer is now cached; retry get_chat to grab the title
            obj3 = await safe_get_chat(uc, chat_id)
            if obj3:
                title = getattr(obj3, "title", None) or title
            return True, title, None
    except Exception as e:
        logger.info(f"[check_access] S3 chat_history failed: {e}")

    # S4: get_messages with a known low ID — sometimes resolves when history fails
    try:
        msgs = await uc.get_messages(chat_id, message_ids=[1, 2, 3])
        if msgs is not None:
            logger.info(f"[check_access] S4 get_messages OK for {chat_id}")
            obj4 = await safe_get_chat(uc, chat_id)
            if obj4:
                title = getattr(obj4, "title", None) or title
            return True, title, None
    except Exception as e:
        logger.info(f"[check_access] S4 get_messages failed: {e}")

    # All strategies failed — user is genuinely not a member or peer unresolvable
    try:
        me  = await uc.get_me()
        err = (f"❌ **Not a member!**\n"
               f"`{me.first_name}` is not in this group/channel.\n"
               f"Please join it first, then send the link again.")
    except Exception:
        err = "❌ Cannot access this chat."
    logger.warning(f"[check_access] All strategies failed for {chat_id}")
    return False, title, err


# ═══════════════════════════════════════════
# FETCH MESSAGES
# ═══════════════════════════════════════════
async def fetch_one(client, chat_id, message_id):
    r = await safe_get_messages(client, chat_id, message_id)
    if isinstance(r, list):
        for m in r:
            if m and not getattr(m, "empty", True) and m.id == message_id:
                return m
        return None
    if r and not getattr(r, "empty", True):
        return r
    return None


def _belongs_to_topic(msg, topic_id: int) -> bool:
    """Return True if msg belongs to the given topic."""
    if msg is None or getattr(msg, "empty", False):
        return False
    tid = getattr(msg, "message_thread_id", None)
    if tid == topic_id:
        return True
    if msg.id == topic_id:
        return True
    reply_to = getattr(msg, "reply_to_message_id", None)
    if reply_to == topic_id:
        return True
    reply_top = getattr(msg, "reply_to_top_message_id", None)
    if reply_top == topic_id:
        return True
    return False


async def fetch_topic_msgs(client, chat_id, topic_id: int, limit: int,
                           start_from_msg_id: int = None):
    """
    Fetch up to `limit` messages belonging to `topic_id`.
    If start_from_msg_id is given, only fetch messages with id >= start_from_msg_id.

    Returns messages sorted by id (ascending = oldest first).
    All content types are included (video, photo, document, text, etc).

    Strategies:
      S1: get_discussion_replies (fastest, Telegram API native)
      S2: chat history scan (fallback for non-forum groups)
      S3: ID-range scan (brute force, works when S1/S2 fail)
      S4: Direct sequential fetch from start_from_msg_id (last resort)
    """
    seen   = set()
    result = []
    start  = start_from_msg_id or 0
    logger.info(f"fetch_topic_msgs: chat={chat_id} topic={topic_id} limit={limit} start_from={start or 'beginning'}")

    # S1: get_discussion_replies
    try:
        logger.info(f"S1 discussion_replies: chat={chat_id} topic={topic_id} want={limit}")
        all_replies = []
        async for msg in client.get_discussion_replies(chat_id, topic_id):
            if msg and not getattr(msg, "empty", False):
                all_replies.append(msg)

        if all_replies:
            # Sort ascending by message id
            all_replies.sort(key=lambda m: m.id)

            # Filter: only messages >= start_from_msg_id
            if start:
                all_replies = [m for m in all_replies if m.id >= start]

            # Take first `limit` messages that have content
            for msg in all_replies:
                if msg.id not in seen and _has_content(msg):
                    seen.add(msg.id)
                    result.append(msg)
                if len(result) >= limit:
                    break

            if result:
                logger.info(f"S1 -> {len(result)} messages with content")
                return result[:limit]

            logger.info(f"S1 -> got {len(all_replies)} replies but 0 with content after filtering")
            result.clear()
            seen.clear()
    except Exception as e:
        logger.info(f"S1 failed ({type(e).__name__}): {e}")

    # S2: history scan (only when no start point)
    if not start:
        try:
            logger.info(f"S2 history scan: chat={chat_id} topic={topic_id} want={limit}")
            scanned  = 0
            max_scan = max(limit * 50, 5000)

            async for msg in client.get_chat_history(chat_id, limit=max_scan):
                scanned += 1
                if not msg or getattr(msg, "empty", False):
                    continue
                if _belongs_to_topic(msg, topic_id) and _has_content(msg):
                    if msg.id not in seen:
                        seen.add(msg.id)
                        result.append(msg)
                    if len(result) >= limit:
                        break

            if result:
                result.sort(key=lambda m: m.id)
                logger.info(f"S2 -> {len(result)} (scanned {scanned})")
                return result[:limit]
            logger.info(f"S2 scanned {scanned} messages, found 0 in topic {topic_id}")
        except Exception as e:
            logger.info(f"S2 failed ({type(e).__name__}): {e}")
    else:
        logger.info(f"S2 skipped (start_from={start} set)")

    # S3: ID-range scan with topic filter
    try:
        scan_start = max(start, topic_id) if start else topic_id
        logger.info(f"S3 ID-range scan: chat={chat_id} starting at {scan_start}")
        batch_sz = 200
        cursor   = scan_start
        rounds   = 0
        max_rounds = max((limit * 10) // batch_sz + 5, 30)
        empty_rounds = 0

        while len(result) < limit and rounds < max_rounds:
            ids    = list(range(cursor, cursor + batch_sz))
            rounds += 1
            cursor += batch_sz
            msgs_r = await safe_get_messages(client, chat_id, ids)
            if msgs_r is None:
                break
            if not isinstance(msgs_r, list):
                msgs_r = [msgs_r]

            got_any = False
            for msg in msgs_r:
                if not msg or getattr(msg, "empty", False):
                    continue
                got_any = True
                if _belongs_to_topic(msg, topic_id) and _has_content(msg):
                    if msg.id not in seen:
                        seen.add(msg.id)
                        result.append(msg)
                    if len(result) >= limit:
                        break

            if not got_any:
                empty_rounds += 1
                if empty_rounds >= 5:
                    break
            else:
                empty_rounds = 0

            await asyncio.sleep(0.3)

        if result:
            result.sort(key=lambda m: m.id)
            logger.info(f"S3 -> {len(result)}")
            return result[:limit]
    except Exception as e:
        logger.info(f"S3 failed ({type(e).__name__}): {e}")

    # S4: Direct sequential fetch from start_from_msg_id (no topic filter)
    if start:
        try:
            logger.info(f"S4 direct fetch: chat={chat_id} start={start} want={limit}")
            cursor = start
            batch_sz = 200
            empty_rounds = 0
            max_rounds = max((limit * 5) // batch_sz + 5, 30)
            rounds = 0

            while len(result) < limit and rounds < max_rounds:
                ids = list(range(cursor, cursor + batch_sz))
                rounds += 1
                cursor += batch_sz
                msgs_r = await safe_get_messages(client, chat_id, ids)
                if msgs_r is None:
                    break
                if not isinstance(msgs_r, list):
                    msgs_r = [msgs_r]

                got_any = False
                for msg in msgs_r:
                    if not msg or getattr(msg, "empty", False):
                        continue
                    if not _has_content(msg):
                        continue
                    got_any = True
                    if msg.id not in seen:
                        seen.add(msg.id)
                        result.append(msg)
                    if len(result) >= limit:
                        break

                if not got_any:
                    empty_rounds += 1
                    if empty_rounds >= 5:
                        break
                else:
                    empty_rounds = 0
                await asyncio.sleep(0.2)

            if result:
                result.sort(key=lambda m: m.id)
                logger.info(f"S4 -> {len(result)} messages")
                return result[:limit]
        except Exception as e:
            logger.info(f"S4 failed ({type(e).__name__}): {e}")

    logger.warning(f"All strategies failed for topic {topic_id} in chat {chat_id}")
    return []


async def get_serial_msgs(client, chat_id, limit):
    """Fetch the latest `limit` messages with any content (serial mode)."""
    msgs = []
    try:
        async for msg in client.get_chat_history(chat_id, limit=limit * 3):
            if msg and not getattr(msg, "empty", False) and get_ct(msg):
                msgs.append(msg)
            if len(msgs) >= limit:
                break
    except Exception as e:
        logger.error(f"Serial fetch error: {e}")
    msgs.reverse()
    return msgs


# ═══════════════════════════════════════════
# INVITE JOIN
# ═══════════════════════════════════════════
async def join_inv(client, link, uid):
    try:
        r = await client.join_chat(link)
        return True, getattr(r, "title", "OK")
    except UserAlreadyParticipant:
        return True, "Already in"
    except InviteHashExpired:
        return False, "Expired"
    except InviteHashInvalid:
        return False, "Invalid"
    except FloodWait as e:
        return False, f"Wait {e.value}s"
    except Exception as e:
        return False, type(e).__name__
