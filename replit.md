# Telegram File Receiver

A private Telegram bot that receives files and saves them into the project for later inspection.

## Run & Operate

- `pnpm --filter @workspace/api-server run dev` — run the API server (port 5000)
- `pnpm run typecheck` — full typecheck across all packages
- `pnpm run build` — typecheck + build all packages
- `pnpm --filter @workspace/api-spec run codegen` — regenerate API hooks and Zod schemas from the OpenAPI spec
- `pnpm --filter @workspace/db run push` — push DB schema changes (dev only)
- Required env: `DATABASE_URL` — Postgres connection string
- Required secret: `TELEGRAM_BOT_TOKEN`
- Optional env: `TELEGRAM_OWNER_CHAT_ID` to pre-authorize one private Telegram chat
- Optional env: `FILE_RECEIVER_DIR` to change the saved-file directory
- Main bot runtime: `artifacts/api-server/bot-runtime/`
- Main bot launcher: `artifacts/api-server/run-bot.sh`
- Published keep-alive endpoint: `/health` (use the published `https://*.replit.app/health` URL)

## Stack

- pnpm workspaces, Node.js 24, TypeScript 5.9
- API: Express 5
- DB: PostgreSQL + Drizzle ORM
- Validation: Zod (`zod/v4`), `drizzle-zod`
- API codegen: Orval (from OpenAPI spec)
- Build: esbuild (CJS bundle)

## Where things live

- Telegram polling and file saving: `artifacts/api-server/src/telegramReceiver.ts`
- Receiver status endpoint: `GET /api/telegram/status`
- Received files and JSONL metadata: `received-files/`

## Architecture decisions

- Uses Telegram long polling, so it does not require a public webhook URL.
- The first private chat that sends `/start` becomes the owner when `TELEGRAM_OWNER_CHAT_ID` is not set.
- Files are saved with sanitized names and message IDs; metadata is appended to `received-files.jsonl`.
- The main bot runs from a separate runtime copy; the original received files are preserved unchanged.

## Product

The bot accepts documents, photos, videos, audio, voice messages, and animations, then confirms each saved file in Telegram.

## User preferences

_Populate as you build — explicit user instructions worth remembering across sessions._

## Gotchas

_Populate as you build — sharp edges, "always run X before Y" rules._

## Pointers

- See the `pnpm-workspace` skill for workspace structure, TypeScript setup, and package details
