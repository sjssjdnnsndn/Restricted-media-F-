---
name: Telegram update guards
description: Prevent self-triggered reply loops in Telegram polling bots.
---

Telegram polling handlers should ignore updates authored by bots and should not
reply to ordinary non-command text when the active flow does not require input.

**Why:** A bot-generated or duplicate update can re-enter a broad private
message handler; replying to every non-link message then creates a visible
invalid-link loop.

**How to apply:** Keep bot-authored-update filtering at the message-handler
boundary, and make direct link handling respond only when a Telegram link is
actually present. Preserve explicit batch/setup state prompts separately.