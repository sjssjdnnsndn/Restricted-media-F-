---
name: Single Telegram poller
description: Keep Telegram polling limited to one active deployment instance.
---

Telegram polling must have exactly one active consumer for a bot token. The
development workflow and published deployment can run simultaneously in the
same workspace, and both may consume or process the same bot updates.

**Why:** Running both caused duplicate replies and duplicate files. Auto Scale
can also be unsafe for polling if it starts more than one replica.

**How to apply:** Stop the API development workflow when production is active,
use a production-only session, and keep a database-backed single-poller lease
as a safety net. Prefer an always-running single-instance VM deployment.