---
name: Publishable app registration
description: Replit publishing behavior for workspaces that contain API services and design artifacts.
---

When a workspace contains only an API artifact and design/mockup artifacts, the Publishing panel can report that there is nothing to publish. A real deployable web artifact at the root preview path is required for the publish flow to recognize a user-facing app.

**Why:** The publishing UI classifies artifacts by deployability; an API service alone may not be treated as the publishable application even when its workflow is running.

**How to apply:** Keep the user-facing web artifact at `/`, keep backend services on explicit paths such as `/api`, and use the published domain plus the backend path for external health checks.