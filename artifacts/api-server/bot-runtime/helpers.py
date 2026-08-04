"""
HELPERS - URL parsing, chat ID helpers, content type detection, safe wrappers
"""

import re
import logging
from typing import Optional

logger = logging.getLogger(__name__)

# Import get_ct at module level to avoid circular imports
# It's defined later in this file, so we use a forward reference pattern
# get_ct is used in detect_and_fix_forum


# ═════════════════════════════════════════════
# CHAT ID HELPERS
# ═════════════════════════════════════════════
def make_pyrogram_chat_id(raw_digits: str) -> int:
    clean = raw_digits[-10:] if len(raw_digits) > 10 else raw_digits
    return int(f"-100{clean}")


def get_raw_chat_id(pyrogram_id: int) -> str:
    s = str(abs(pyrogram_id))
    if s.startswith("100") and len(s) > 10:
        return s[3:]
    return s


# ═════════════════════════════════════════════
# SAFE WRAPPERS
# ═════════════════════════════════════════════
async def safe_get_chat(client, chat_id):
    try:
        return await client.get_chat(chat_id)
    except Exception:
        return None


async def safe_get_messages(client, chat_id, msg_ids):
    try:
        return await client.get_messages(chat_id, message_ids=msg_ids)
    except Exception:
        return None


# ═════════════════════════════════════════════
# URL PARSING
# ═════════════════════════════════════════════
def extract_post_info(url: str) -> Optional[dict]:
    if not url:
        return None
    url = url.strip()

    # Pattern 1: t.me/c/DIGITS/DIGITS/DIGITS  (3-part private)
    # This is ALWAYS: chat / topic / message
    m = re.search(r't\.me/c/(\d+)/(\d+)/(\d+)', url)
    if m:
        chat_id  = make_pyrogram_chat_id(m.group(1))
        raw      = get_raw_chat_id(chat_id)
        topic_id = int(m.group(2))
        msg_id   = int(m.group(3))
        logger.info(f"URL PARSE 3-part: chat={chat_id} raw={raw} topic={topic_id} msg={msg_id}")
        return {
            "chat_id":     chat_id,
            "raw_chat_id": raw,
            "topic_id":    topic_id,
            "message_id":  msg_id,
            "is_topic":    True,
            "is_private":  True,
            "is_3part":    True,
        }

    # Pattern 2: t.me/c/DIGITS/DIGITS  (2-part private)
    # Could be topic link OR single message - needs forum check
    m = re.search(r't\.me/c/(\d+)/(\d+)', url)
    if m:
        chat_id = make_pyrogram_chat_id(m.group(1))
        raw     = get_raw_chat_id(chat_id)
        number  = int(m.group(2))
        logger.info(f"URL PARSE 2-part: chat={chat_id} raw={raw} number={number}")
        return {
            "chat_id":          chat_id,
            "raw_chat_id":      raw,
            "number":           number,
            "topic_id":         None,
            "message_id":       number,
            "is_topic":         False,
            "is_private":       True,
            "is_3part":         False,
            "needs_forum_check": True,
        }

    # Pattern 3: t.me/username/DIGITS/DIGITS  (3-part public)
    m = re.search(
        r'(?:t\.me|telegram\.me)/([a-zA-Z][a-zA-Z0-9_]{3,})/(\d+)/(\d+)', url
    )
    if m and m.group(1).lower() not in ("c", "joinchat", "addstickers", "share", "login"):
        return {
            "username":   m.group(1),
            "topic_id":   int(m.group(2)),
            "message_id": int(m.group(3)),
            "is_topic":   True,
            "is_private": False,
            "is_3part":   True,
        }

    # Pattern 4: t.me/username/DIGITS  (2-part public)
    m = re.search(
        r'(?:t\.me|telegram\.me)/([a-zA-Z][a-zA-Z0-9_]{3,})/(\d+)', url
    )
    if m and m.group(1).lower() not in ("c", "joinchat", "addstickers", "share", "login"):
        number = int(m.group(2))
        return {
            "username":          m.group(1),
            "number":            number,
            "topic_id":          None,
            "message_id":        number,
            "is_topic":          False,
            "is_private":        False,
            "is_3part":          False,
            "needs_forum_check": True,
        }

    return None


async def detect_and_fix_forum(client, info):
    """
    For 2-part URLs: confirm whether NUMBER is a topic_id (forum) or msg_id.
    Sets is_topic=True and topic_id if forum detected.
    Sets is_topic=False and topic_id=None if NOT a forum.
    """
    if not info.get("needs_forum_check"):
        return info

    chat_id = info.get("chat_id") or info.get("username")
    number  = info.get("number")
    if not chat_id or not number:
        info.pop("needs_forum_check", None)
        return info

    try:
        chat_obj = await safe_get_chat(client, chat_id)
        if not chat_obj and info.get("is_private"):
            from downloader import _warm_peer_via_dialogs
            logger.info(f"FORUM CHECK: peer not cached for {chat_id}, trying dialog warm-up")
            _, found = await _warm_peer_via_dialogs(client, chat_id)
            if found:
                chat_obj = await safe_get_chat(client, chat_id)
        is_forum = getattr(chat_obj, "is_forum", False) if chat_obj else False
        logger.info(f"FORUM CHECK: chat={chat_id} is_forum={is_forum} title={getattr(chat_obj,'title','?') if chat_obj else 'N/A'}")

        if is_forum:
            # Chat is a forum -> number is a topic_id
            info["topic_id"] = number
            info["is_topic"] = True
            info["message_id"] = number
            logger.info(f"-> FORUM confirmed! topic_id={number}")
        else:
            # Not detected as forum via is_forum flag
            # But we still need to check the message itself
            info["topic_id"] = None
            info["is_topic"] = False
            info["message_id"] = number
            try:
                msg = await client.get_messages(chat_id, message_ids=number)
                if msg and not getattr(msg, "empty", False):
                    forum_created = getattr(msg, "forum_topic_created", None)
                    thread = getattr(msg, "message_thread_id", None)
                    # Check service attribute for topic creation messages
                    service = getattr(msg, "service", None)

                    if forum_created:
                        info["topic_id"] = number
                        info["is_topic"] = True
                        logger.info(f"-> forum_topic_created detected: topic_id={number}")
                    elif thread:
                        info["topic_id"] = thread
                        info["is_topic"] = True
                        logger.info(f"-> Thread detected: topic_id={thread} (msg={number})")
                    elif service is not None:
                        # Service message (topic creation, etc) -> treat as topic
                        info["topic_id"] = number
                        info["is_topic"] = True
                        logger.info(f"-> Service message detected (service={service}): topic_id={number}")
                    elif not get_ct(msg):
                        # Message has no downloadable content -> likely a topic header
                        info["topic_id"] = number
                        info["is_topic"] = True
                        logger.info(f"-> No content in msg (likely topic header): topic_id={number}")
                    else:
                        # Has content, no thread -> plain message
                        logger.info(f"-> Has content, no thread: treating as plain message_id={number}")
                elif msg and getattr(msg, "empty", False):
                    # Empty message at this ID could be a topic header
                    # Try to check if there are discussion replies
                    try:
                        reply_count = 0
                        async for _ in client.get_discussion_replies(chat_id, number):
                            reply_count += 1
                            if reply_count >= 1:
                                break
                        if reply_count > 0:
                            info["topic_id"] = number
                            info["is_topic"] = True
                            logger.info(f"-> Empty msg but has discussion replies: topic_id={number}")
                        else:
                            logger.info(f"-> Empty msg, no replies: treating as plain message_id={number}")
                    except Exception:
                        # get_discussion_replies failed -> not a topic
                        logger.info(f"-> Empty msg, discussion check failed: plain message_id={number}")
            except Exception as ex:
                logger.warning(f"Message fetch for thread check failed: {ex}")
                logger.info(f"-> Keeping as plain message_id={number} (forum check failed)")
    except Exception as e:
        logger.warning(f"Forum check failed ({type(e).__name__}): {e}")

    info.pop("needs_forum_check", None)
    return info


def extract_invite_link(text):
    if not text:
        return None
    for p in [
        r'(?:https?://)?(?:t\.me|telegram\.me)/\+([A-Za-z0-9_-]+)',
        r'(?:https?://)?(?:t\.me|telegram\.me)/joinchat/([A-Za-z0-9_-]+)',
    ]:
        m = re.search(p, text)
        if m:
            return text[m.start():m.end()]
    return None


def extract_channel_username(text):
    if not text:
        return None
    m = re.search(r'(?:https?://)?(?:t\.me|telegram\.me)/([a-zA-Z0-9_]+)(?:\?|$)', text)
    if m and m.group(1).lower() not in ("joinchat", "addstickers", "share", "login", "c"):
        return m.group(1)
    m = re.search(r'@([a-zA-Z0-9_]+)', text)
    return m.group(1) if m else None


# ═════════════════════════════════════════════
# CONTENT TYPE DETECTION
# ═════════════════════════════════════════════
def get_ct(msg):
    if not msg or getattr(msg, "empty", False):
        return None
    if msg.photo:      return "photo"
    if msg.video:      return "video"
    if msg.document:   return "document"
    if msg.audio:      return "audio"
    if msg.voice:      return "voice"
    if msg.video_note: return "video_note"
    if msg.animation:  return "animation"
    if msg.sticker:    return "sticker"
    if msg.contact:    return "contact"
    if msg.location or msg.venue: return "location"
    if msg.poll:       return "poll"
    if msg.dice:       return "dice"
    if msg.text:       return "text"
    if msg.caption:    return "caption_only"
    if getattr(msg, "media", None):
        return "document"
    return None


def _has_content(msg) -> bool:
    return get_ct(msg) is not None


_EM = {
    "photo": "\U0001f5bc", "video": "\U0001f3ac", "document": "\U0001f4c4", "audio": "\U0001f3b5",
    "voice": "\U0001f3a4", "video_note": "\u2b55", "animation": "\U0001f39e", "sticker": "\U0001f3f7",
    "contact": "\U0001f464", "location": "\U0001f4cd", "poll": "\U0001f4ca", "dice": "\U0001f3b2",
    "text": "\U0001f4dd", "caption_only": "\U0001f4dd",
}


def em(ct):
    return _EM.get(ct, "\U0001f4e6")


def build_link(chat, msg_id, topic_id=None):
    if isinstance(chat, int):
        raw = get_raw_chat_id(chat)
        if topic_id:
            return f"https://t.me/c/{raw}/{topic_id}/{msg_id}"
        return f"https://t.me/c/{raw}/{msg_id}"
    if topic_id:
        return f"https://t.me/{chat}/{topic_id}/{msg_id}"
    return f"https://t.me/{chat}/{msg_id}"
