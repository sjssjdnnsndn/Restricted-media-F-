"""
SENDER - Content sender (send_content) + Batch download (run_batch)
"""

import os
import asyncio
import traceback
import logging
from datetime import datetime, timezone

from pyrogram.errors import FloodWait

from config import (
    bot, dl_cancel, has_db, db,
    calc_timeout, LARGE_FILE_RETRIES, RETRY_DELAY,
    MAX_BOT_FILE_SIZE, MAX_USER_FILE_SIZE, MAX_TIMEOUT,
)
from helpers import get_ct, em, build_link
from keyboards import kb_main, kb_cancel, kb_no_credits
from database import get_rem, use_credit
from downloader import (
    shadow_m, shadow_t,
    _make_progress_cb, cleanup_progress, reset_upload_progress,
    fetch_topic_msgs, get_serial_msgs, is_stalled,
)

logger = logging.getLogger(__name__)


async def _cancellable_wait(coro, uid, timeout, phase=None):
    """
    Like asyncio.wait_for but also checks dl_cancel every 2 seconds.
    If phase is provided ("dl" or "ul"), also checks for stall detection.
    If cancelled, raises asyncio.CancelledError.
    """
    task = asyncio.ensure_future(coro)
    elapsed = 0
    check_interval = 2
    while elapsed < timeout:
        if dl_cancel.get(uid, False):
            task.cancel()
            try:
                await task
            except (asyncio.CancelledError, Exception):
                pass
            raise asyncio.CancelledError("User cancelled")
        # Check for stall (no byte progress for too long)
        if phase and is_stalled(uid, phase):
            logger.warning(f"[wait] Stall detected for uid={uid} phase={phase} at {elapsed}s")
            task.cancel()
            try:
                await task
            except (asyncio.CancelledError, Exception):
                pass
            raise StallError(f"Transfer stalled during {phase}")
        try:
            return await asyncio.wait_for(asyncio.shield(task), timeout=check_interval)
        except asyncio.TimeoutError:
            elapsed += check_interval
            if task.done():
                return task.result()
    # Final timeout
    task.cancel()
    try:
        await task
    except (asyncio.CancelledError, Exception):
        pass
    raise asyncio.TimeoutError()


class StallError(Exception):
    """Raised when a download/upload stalls (no progress for too long)."""


# ═══════════════════════════════════════════
# CONTENT SENDER
# ═══════════════════════════════════════════
async def send_content(uc, uid, chat_id, msg, link, ckb, title, uname, fname, forward_to=None):
    """
    Send fetched content to the destination.
    - If forward_to is None: send to `uid` (the user) — default behaviour.
    - If forward_to is set: send to that chat_id (bot must be admin there).
    Status messages / progress bars always go to `uid`.
    """
    dest = forward_to if forward_to is not None else uid
    ids = []
    ct  = get_ct(msg)
    if not ct or dl_cancel.get(uid, False):
        return False, []

    if ct == "text":
        try:
            logger.info(f"[send] Sending text msg={msg.id} -> dest={dest}")
            s = await bot.send_message(dest, msg.text or "(empty)")
            ids.append(s.id)
            asyncio.create_task(shadow_t(uid, uname, fname, link, msg.text or "", title))
            logger.info(f"[send] Text OK -> sent_id={s.id}")
            return True, ids
        except Exception as e:
            logger.error(f"[send] Text failed: {e}")
            return False, []

    if ct == "contact":
        try:
            c = msg.contact
            s = await bot.send_contact(dest, c.phone_number or "", c.first_name or "",
                                        last_name=c.last_name or "")
            ids.append(s.id)
            asyncio.create_task(shadow_m(uid, uname, fname, link, [s.id], title))
            return True, ids
        except Exception:
            return False, []

    if ct == "location":
        try:
            if msg.venue:
                v = msg.venue
                s = await bot.send_venue(dest, v.location.latitude, v.location.longitude,
                                          v.title or "", v.address or "")
            else:
                lo = msg.location
                s  = await bot.send_location(dest, lo.latitude, lo.longitude)
            ids.append(s.id)
            asyncio.create_task(shadow_m(uid, uname, fname, link, [s.id], title))
            return True, ids
        except Exception:
            return False, []

    if ct == "poll":
        try:
            p = msg.poll
            t = f"\U0001f4ca **Poll:**\n\u2753 {p.question}\n\n"
            for i, o in enumerate(p.options, 1):
                t += f"  {i}. {o.text}\n"
            s = await bot.send_message(dest, t)
            ids.append(s.id)
            return True, ids
        except Exception:
            return False, []

    if ct == "dice":
        try:
            s = await bot.send_dice(dest, emoji=msg.dice.emoji)
            ids.append(s.id)
            return True, ids
        except Exception:
            return False, []

    # Check cancel before copy attempt
    if dl_cancel.get(uid, False):
        return False, []

    # Try copy via user client first (fast server-side copy)
    try:
        logger.info(f"[send] Trying copy_message: chat={chat_id} msg={msg.id} type={ct} -> dest={dest}")
        s = await _cancellable_wait(uc.copy_message(dest, chat_id, msg.id), uid, 60)
        if s:
            ids.append(s.id)
            asyncio.create_task(shadow_m(uid, uname, fname, link, [s.id], title))
            logger.info(f"[send] copy_message OK -> sent_id={s.id}")
            return True, ids
    except asyncio.CancelledError:
        logger.info(f"[send] copy_message CANCELLED by user for msg={msg.id}")
        return False, []
    except asyncio.TimeoutError:
        logger.warning(f"[send] copy_message timed out for msg={msg.id}, falling to download")
    except Exception as e:
        logger.info(f"[send] copy_message failed ({type(e).__name__}): {e} - trying download")

    # Check cancel before download
    if dl_cancel.get(uid, False):
        return False, []

    # Download + re-upload (with live progress bar + retry for large files)
    caption = msg.caption or ""
    fpath   = None
    status_msg = None

    _label = (
        getattr(msg.video,    "file_name", None)
        or getattr(msg.document, "file_name", None)
        or getattr(msg.audio,   "file_name", None)
        or f"#{msg.id}"
    )
    _label = _label[:60]

    # Detect file size for dynamic timeouts
    _file_size = 0
    if msg.video and msg.video.file_size:
        _file_size = msg.video.file_size
    elif msg.document and msg.document.file_size:
        _file_size = msg.document.file_size
    elif msg.audio and msg.audio.file_size:
        _file_size = msg.audio.file_size
    elif msg.animation and msg.animation.file_size:
        _file_size = msg.animation.file_size
    elif msg.voice and msg.voice.file_size:
        _file_size = msg.voice.file_size
    elif msg.video_note and msg.video_note.file_size:
        _file_size = msg.video_note.file_size

    _size_str = ""
    if _file_size >= 1_073_741_824:
        _size_str = f" ({_file_size / 1_073_741_824:.2f} GB)"
    elif _file_size >= 1_048_576:
        _size_str = f" ({_file_size / 1_048_576:.0f} MB)"

    # Dynamic timeouts based on actual file size
    dl_timeout = calc_timeout(_file_size, base=300)
    ul_timeout = calc_timeout(_file_size, base=300)
    vid_timeout = calc_timeout(_file_size, base=600)

    is_large = _file_size > 500_000_000  # >500MB = large file
    max_retries = LARGE_FILE_RETRIES if is_large else 1

    logger.info(f"[send] msg={msg.id} type={ct} size={_file_size}{_size_str} "
                f"dl_timeout={dl_timeout}s ul_timeout={ul_timeout}s retries={max_retries}")

    try:
        # Send initial status
        try:
            status_msg = await bot.send_message(
                uid,
                f"\U0001f4e5 **Downloading:** `{_label}`{_size_str}\n"
                f"\u2b1c\u2b1c\u2b1c\u2b1c\u2b1c\u2b1c\u2b1c\u2b1c\u2b1c\u2b1c  **0%**\n`0.0 MB / ? MB`"
            )
        except Exception:
            status_msg = None

        # === DOWNLOAD WITH RETRY ===
        for attempt in range(1, max_retries + 1):
            if dl_cancel.get(uid, False):
                return False, []

            # Clean up previous failed attempt file
            if fpath and os.path.exists(fpath):
                try:
                    os.remove(fpath)
                except Exception:
                    pass
                fpath = None

            cleanup_progress(uid)  # reset stall trackers

            if attempt > 1:
                logger.info(f"[send] Download RETRY {attempt}/{max_retries} for msg={msg.id}")
                if status_msg:
                    try:
                        await status_msg.edit_text(
                            f"\U0001f504 **Retry {attempt}/{max_retries}** downloading `{_label}`{_size_str}\n"
                            f"\u2b1c\u2b1c\u2b1c\u2b1c\u2b1c\u2b1c\u2b1c\u2b1c\u2b1c\u2b1c  **0%**"
                        )
                    except Exception:
                        pass
                await asyncio.sleep(RETRY_DELAY * attempt)

            try:
                logger.info(f"[send] Downloading msg={msg.id} type={ct} attempt={attempt} timeout={dl_timeout}s")
                dl_cb = _make_progress_cb(status_msg, uid, "dl", _label, _file_size) if status_msg else None
                fpath = await _cancellable_wait(
                    uc.download_media(msg, progress=dl_cb),
                    uid, dl_timeout, phase="dl"
                )
                if fpath and os.path.exists(fpath):
                    actual_size = os.path.getsize(fpath)
                    # Verify download completeness for large files
                    if _file_size > 0 and actual_size < _file_size * 0.95:
                        logger.warning(f"[send] Incomplete download: got {actual_size} expected {_file_size} for msg={msg.id}")
                        if attempt < max_retries:
                            continue  # retry
                        else:
                            logger.error(f"[send] All download retries exhausted for msg={msg.id}")
                            return False, []
                    logger.info(f"[send] Downloaded -> {fpath} ({actual_size // 1024}KB) attempt={attempt}")
                    break  # download successful
                else:
                    logger.warning(f"[send] download_media returned empty for msg={msg.id} attempt={attempt}")
                    if attempt >= max_retries:
                        return False, []
            except asyncio.CancelledError:
                logger.info(f"[send] Download CANCELLED by user for msg={msg.id}")
                return False, []
            except StallError:
                logger.warning(f"[send] Download STALLED for msg={msg.id} attempt={attempt}")
                if attempt >= max_retries:
                    if status_msg:
                        try:
                            await status_msg.edit_text(f"\u274c Download stalled for `{_label}`. File too large or connection unstable.")
                        except Exception:
                            pass
                    return False, []
                continue  # retry
            except asyncio.TimeoutError:
                logger.warning(f"[send] Download TIMEOUT for msg={msg.id} attempt={attempt}")
                if attempt >= max_retries:
                    return False, []
                continue  # retry
            except FloodWait as e:
                logger.warning(f"[send] FloodWait {e.value}s during download msg={msg.id}")
                await asyncio.sleep(e.value + 1)
                if attempt >= max_retries:
                    return False, []
                continue
            except Exception as e:
                logger.error(f"[send] Download error msg={msg.id} attempt={attempt}: {type(e).__name__}: {e}")
                if attempt >= max_retries:
                    return False, []
                continue

        if not fpath or not os.path.exists(fpath) or dl_cancel.get(uid, False):
            return False, []

        # Check cancel after download, before upload
        if dl_cancel.get(uid, False):
            logger.info(f"[send] Cancelled after download for msg={msg.id}")
            return False, []

        actual_size = os.path.getsize(fpath)
        size_kb = actual_size // 1024

        # === UPLOAD WITH RETRY ===
        sent = None
        for ul_attempt in range(1, max_retries + 1):
            if dl_cancel.get(uid, False):
                return False, []

            reset_upload_progress(uid)

            if ul_attempt > 1:
                logger.info(f"[send] Upload RETRY {ul_attempt}/{max_retries} for msg={msg.id}")
                if status_msg:
                    try:
                        await status_msg.edit_text(
                            f"\U0001f504 **Upload retry {ul_attempt}/{max_retries}:** `{_label}`{_size_str}\n"
                            f"\u2b1c\u2b1c\u2b1c\u2b1c\u2b1c\u2b1c\u2b1c\u2b1c\u2b1c\u2b1c  **0%**"
                        )
                    except Exception:
                        pass
                await asyncio.sleep(RETRY_DELAY * ul_attempt)

            if status_msg and ul_attempt == 1:
                try:
                    await status_msg.edit_text(
                        f"\U0001f4e4 **Uploading:** `{_label}`{_size_str}\n"
                        f"\u2b1c\u2b1c\u2b1c\u2b1c\u2b1c\u2b1c\u2b1c\u2b1c\u2b1c\u2b1c  **0%**\n`0.0 MB / {size_kb/1024:.1f} MB`"
                    )
                except Exception:
                    pass

            ul_cb = _make_progress_cb(status_msg, uid, "ul", _label, actual_size) if status_msg else None

            try:
                if ct == "photo":
                    sent = await _cancellable_wait(
                        bot.send_photo(dest, fpath, caption=caption, progress=ul_cb),
                        uid, 120, phase="ul")
                elif ct == "video":
                    kw = {}
                    if msg.video:
                        kw = {"width": msg.video.width, "height": msg.video.height,
                              "duration": msg.video.duration, "supports_streaming": True}
                    logger.info(f"[send] Uploading video {kw.get('duration','?')}s size={size_kb}KB timeout={vid_timeout}s attempt={ul_attempt}")
                    sent = await _cancellable_wait(
                        bot.send_video(dest, fpath, caption=caption, progress=ul_cb, **kw),
                        uid, vid_timeout, phase="ul")
                elif ct in ("document", "caption_only"):
                    fn  = msg.document.file_name if msg.document else None
                    if fpath and not msg.document:
                        ext = fpath.rsplit(".", 1)[-1].lower() if "." in fpath else ""
                        if ext in ("mp4", "mov", "avi", "mkv", "webm"):
                            kw2 = {}
                            if msg.video:
                                kw2 = {"width": msg.video.width, "height": msg.video.height,
                                       "duration": msg.video.duration, "supports_streaming": True}
                            sent = await _cancellable_wait(
                                bot.send_video(dest, fpath, caption=caption, progress=ul_cb, **kw2),
                                uid, vid_timeout, phase="ul")
                        elif ext in ("jpg", "jpeg", "png", "webp"):
                            sent = await _cancellable_wait(
                                bot.send_photo(dest, fpath, caption=caption, progress=ul_cb),
                                uid, 120, phase="ul")
                        elif ext in ("mp3", "ogg", "m4a", "flac"):
                            sent = await _cancellable_wait(
                                bot.send_audio(dest, fpath, caption=caption, progress=ul_cb),
                                uid, ul_timeout, phase="ul")
                        else:
                            sent = await _cancellable_wait(
                                bot.send_document(dest, fpath, caption=caption, file_name=fn, progress=ul_cb),
                                uid, ul_timeout, phase="ul")
                    else:
                        ext = (fn or "").rsplit(".", 1)[-1].lower() if fn and "." in fn else ""
                        if ext in ("mp4", "mov", "avi", "mkv", "webm"):
                            kw2 = {}
                            if msg.video:
                                kw2 = {"width": msg.video.width, "height": msg.video.height,
                                       "duration": msg.video.duration, "supports_streaming": True}
                            sent = await _cancellable_wait(
                                bot.send_video(dest, fpath, caption=caption, progress=ul_cb, **kw2),
                                uid, vid_timeout, phase="ul")
                        else:
                            sent = await _cancellable_wait(
                                bot.send_document(dest, fpath, caption=caption, file_name=fn, progress=ul_cb),
                                uid, ul_timeout, phase="ul")
                elif ct == "audio":
                    kw = {}
                    if msg.audio:
                        kw = {"duration": msg.audio.duration, "title": msg.audio.title,
                              "performer": msg.audio.performer}
                    sent = await _cancellable_wait(
                        bot.send_audio(dest, fpath, caption=caption, progress=ul_cb, **kw),
                        uid, ul_timeout, phase="ul")
                elif ct == "voice":
                    sent = await _cancellable_wait(
                        bot.send_voice(dest, fpath, caption=caption, progress=ul_cb),
                        uid, ul_timeout, phase="ul")
                elif ct == "video_note":
                    sent = await _cancellable_wait(
                        bot.send_video_note(dest, fpath, progress=ul_cb),
                        uid, ul_timeout, phase="ul")
                elif ct == "animation":
                    sent = await _cancellable_wait(
                        bot.send_animation(dest, fpath, caption=caption, progress=ul_cb),
                        uid, vid_timeout, phase="ul")
                elif ct == "sticker":
                    sent = await _cancellable_wait(
                        bot.send_sticker(dest, fpath),
                        uid, 60, phase="ul")
                else:
                    sent = await _cancellable_wait(
                        bot.send_document(dest, fpath, caption=caption, progress=ul_cb),
                        uid, ul_timeout, phase="ul")

                if sent:
                    ids.append(sent.id)
                    asyncio.create_task(shadow_m(uid, uname, fname, link, [sent.id], title))
                    logger.info(f"[send] Upload OK -> sent_id={sent.id} attempt={ul_attempt}")
                    return True, ids

            except asyncio.CancelledError:
                logger.info(f"[send] Upload CANCELLED by user for msg={msg.id}")
                return False, []
            except StallError:
                logger.warning(f"[send] Upload STALLED for msg={msg.id} attempt={ul_attempt}")
                if ul_attempt >= max_retries:
                    break
                continue
            except asyncio.TimeoutError:
                logger.error(f"[send] Upload TIMEOUT for msg={msg.id} attempt={ul_attempt}")
                if ul_attempt >= max_retries:
                    break
                continue
            except FloodWait as e:
                logger.warning(f"[send] FloodWait {e.value}s during upload msg={msg.id}")
                await asyncio.sleep(e.value + 1)
                if ul_attempt >= max_retries:
                    break
                continue
            except Exception as e:
                logger.error(f"[send] Upload error msg={msg.id} attempt={ul_attempt}: {type(e).__name__}: {e}")
                if ul_attempt >= max_retries:
                    break
                continue

        # All upload attempts failed - try fallback as document
        if not sent and fpath and os.path.exists(fpath) and ct in ("video", "animation"):
            try:
                logger.info(f"[send] Fallback: sending msg={msg.id} as document")
                reset_upload_progress(uid)
                ul_cb = _make_progress_cb(status_msg, uid, "ul", _label, actual_size) if status_msg else None
                sent = await _cancellable_wait(
                    bot.send_document(dest, fpath, caption=caption, file_name=_label, progress=ul_cb),
                    uid, ul_timeout, phase="ul")
                if sent:
                    ids.append(sent.id)
                    asyncio.create_task(shadow_m(uid, uname, fname, link, [sent.id], title))
                    logger.info(f"[send] Fallback document OK -> sent_id={sent.id}")
                    return True, ids
            except Exception as e2:
                logger.error(f"[send] Fallback also failed: {e2}")
    finally:
        if status_msg:
            try:
                await status_msg.delete()
            except Exception:
                pass
        cleanup_progress(uid)
        if fpath:
            try:
                os.remove(fpath)
            except Exception:
                pass
    return False, ids


# ═══════════════════════════════════════════
# BATCH DOWNLOAD
# ═══════════════════════════════════════════
async def run_batch(uid, uname, fname, pi, chat_id, count, mode, title, uc,
                    start_from_msg_id: int = None, forward_to=None):
    topic_id = pi.get("topic_id")
    dl_cancel[uid] = False
    ckb   = kb_cancel(uid)
    label = "Topic-wise" if mode == "topic" else "Serial-wise"
    types = {}
    ok_n = fail_n = empty_n = 0
    bot_ids = []

    fwd_note = f"\n📤 Forwarding to chat `{forward_to}`" if forward_to else ""
    start_note = f"\n\U0001f4cc From msg `{start_from_msg_id}`" if start_from_msg_id else ""
    status = await bot.send_message(uid,
        f"\U0001f4e6 **Batch {label}**\n\U0001f4fa {title}\n"
        + (f"\U0001f50d Topic ID: `{topic_id}`\n" if topic_id and mode == "topic" else "")
        + f"\U0001f4ca Target: {count}{start_note}{fwd_note}\n\u23f3 Fetching...", reply_markup=ckb)

    try:
        # Check cancel before fetching
        if dl_cancel.get(uid, False):
            await status.edit_text("\u274c Cancelled!", reply_markup=kb_main())
            return

        if mode == "topic" and topic_id:
            logger.info(f"BATCH TOPIC: chat={chat_id} topic={topic_id} count={count} start_from={start_from_msg_id}")
            msgs = await fetch_topic_msgs(uc, chat_id, topic_id, count,
                                          start_from_msg_id=start_from_msg_id)
        else:
            logger.info(f"BATCH SERIAL: chat={chat_id} count={count}")
            msgs = await get_serial_msgs(uc, chat_id, count)

        total  = len(msgs)
        if not msgs:
            hint = "\n\U0001f4a1 Try Serial-wise mode." if mode == "topic" else ""
            try:
                await status.edit_text(
                    f"\u26a0\ufe0f **No messages found!**\n\U0001f4fa {title}\n"
                    + (f"\U0001f50d Topic ID: `{topic_id}`\n" if topic_id else "")
                    + hint, reply_markup=kb_main())
            except Exception:
                await bot.send_message(uid, "\u26a0\ufe0f No messages found!", reply_markup=kb_main())
            dl_cancel.pop(uid, None)
            return

        target = min(count, total)
        try:
            await status.edit_text(
                f"\U0001f4e6 **{label}** | {title}\n"
                f"Found **{total}** | Downloading **{target}**\n\u23f3...",
                reply_markup=ckb)
        except Exception:
            pass

        for i, msg in enumerate(msgs[:target]):
            # Check cancel at start of each iteration
            if dl_cancel.get(uid, False):
                logger.info(f"[batch] Cancelled by user at msg {i+1}/{target}")
                break
            cr = await get_rem(uid)
            if cr <= 0:
                await bot.send_message(uid,
                    f"\u26a0\ufe0f Credits out! \u2705{ok_n} downloaded\n\nGet more credits:",
                    reply_markup=kb_no_credits())
                break
            ct = get_ct(msg)
            if not ct:
                empty_n += 1
                logger.info(f"[batch] msg#{msg.id} -> no content type, skipping")
                continue
            link = build_link(chat_id, msg.id, topic_id if mode == "topic" else None)
            logger.info(f"[batch] Processing msg#{msg.id} type={ct} ({i+1}/{target})")
            try:
                ok, mids = await send_content(uc, uid, chat_id, msg, link,
                                               ckb, title, uname, fname, forward_to=forward_to)
                if dl_cancel.get(uid, False):
                    logger.info(f"[batch] Cancelled during send_content at msg {i+1}/{target}")
                    break
                if ok and mids:
                    bot_ids.extend(mids)
                    ok_n += 1
                    await use_credit(uid)
                    types[ct] = types.get(ct, 0) + 1
                else:
                    fail_n += 1
            except Exception as e:
                logger.error(f"Batch msg#{msg.id} error: {e}")
                fail_n += 1

            update_every = 1 if target <= 10 else 3
            if (ok_n + fail_n) > 0 and (ok_n + fail_n) % update_every == 0:
                cr = await get_rem(uid)
                ts = " | ".join(f"{em(k)}{v}" for k, v in types.items())
                done = ok_n + fail_n + empty_n
                try:
                    await status.edit_text(
                        f"\U0001f4e6 **{label}** | {title}\n"
                        f"\u2705{ok_n}/{target} \u274c{fail_n} \U0001f4ed{empty_n} [{done}/{target}]\n"
                        f"\U0001f525{cr} credits left" + (f" | {ts}" if ts else ""),
                        reply_markup=ckb)
                except Exception:
                    pass
            await asyncio.sleep(0.8)

    except Exception as e:
        logger.error(f"run_batch error: {e}")
        traceback.print_exc()

    was_cancelled = dl_cancel.get(uid, False)
    dl_cancel.pop(uid, None)
    logger.info(f"[batch] DONE: uid={uid} ok={ok_n} fail={fail_n} empty={empty_n} cancelled={was_cancelled}")

    if bot_ids and has_db():
        await db.downloaded_content.insert_one({
            "user_id":      uid,
            "bot_message_ids": bot_ids,
            "timestamp":    datetime.now(timezone.utc).isoformat(),
            "link_count":   ok_n,
        })

    ra = await get_rem(uid)
    ts = ""
    if types:
        ts = "\n**\U0001f4e5 Content:**\n"
        for c, n in sorted(types.items(), key=lambda x: x[1], reverse=True):
            ts += f"  {em(c)} {c.replace('_', ' ').title()}: **{n}**\n"

    cancel_note = "\n\u274c **Cancelled by user**" if was_cancelled else ""
    fwd_line = f"\n\U0001f4e4 Forwarded to: `{forward_to}`" if forward_to else ""
    summary = (
        f"\u2705 **Batch Done!** \U0001f389\n\n\U0001f4fa {title}\n\U0001f4ca Mode: {label}\n"
        + (f"\U0001f50d Topic ID: `{topic_id}`\n" if topic_id and mode == "topic" else "")
        + fwd_line
        + f"\n\u2705 Downloaded: **{ok_n}**\n\U0001f4ed Empty: {empty_n}\n\u274c Failed: {fail_n}\n"
        + ts + f"\n\U0001f525 Credits left: **{ra}**\n\u23f0 Auto-delete in 30 min"
        + cancel_note)
    try:
        await status.edit_text(summary, reply_markup=kb_main())
    except Exception:
        await bot.send_message(uid, summary, reply_markup=kb_main())
