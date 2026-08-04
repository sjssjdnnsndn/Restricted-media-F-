import { appendFile, mkdir, readFile, rename, writeFile } from "node:fs/promises";
import path from "node:path";
import { logger } from "./lib/logger";

const TELEGRAM_API_BASE = "https://api.telegram.org";
const TELEGRAM_FILE_BASE = "https://api.telegram.org/file";
const LONG_POLL_SECONDS = 25;
const DEFAULT_MAX_FILE_SIZE = 20 * 1024 * 1024;

type TelegramResponse<T> = {
  ok: boolean;
  result?: T;
  description?: string;
  error_code?: number;
};

type TelegramUser = {
  id: number;
  is_bot?: boolean;
  username?: string;
  first_name?: string;
  last_name?: string;
};

type TelegramChat = {
  id: number;
  type: string;
};

type TelegramFile = {
  file_path?: string;
  file_size?: number;
};

type TelegramDocument = {
  file_id: string;
  file_name?: string;
  mime_type?: string;
  file_size?: number;
};

type TelegramPhoto = {
  file_id: string;
  width: number;
  height: number;
  file_size?: number;
};

type TelegramMedia = {
  file_id: string;
  file_name?: string;
  mime_type?: string;
  file_size?: number;
  duration?: number;
};

type TelegramMessage = {
  message_id: number;
  from?: TelegramUser;
  chat: TelegramChat;
  text?: string;
  caption?: string;
  document?: TelegramDocument;
  photo?: TelegramPhoto[];
  video?: TelegramMedia;
  audio?: TelegramMedia;
  voice?: TelegramMedia;
  animation?: TelegramMedia;
};

type TelegramUpdate = {
  update_id: number;
  message?: TelegramMessage;
};

type Attachment = {
  fileId: string;
  originalName: string;
  kind: string;
  mimeType?: string;
  fileSize?: number;
};

type ReceiverState = {
  ownerChatId?: string;
};

type ReceiverStatus = {
  enabled: boolean;
  running: boolean;
  ownerConfigured: boolean;
  receivedCount: number;
};

const receiverDirectory = path.resolve(
  process.env.FILE_RECEIVER_DIR?.trim() || "received-files",
);
const statePath = path.join(receiverDirectory, ".receiver-state.json");
const metadataPath = path.join(receiverDirectory, "received-files.jsonl");
const maxFileSize = parseMaxFileSize();

let status: ReceiverStatus = {
  enabled: Boolean(process.env.TELEGRAM_BOT_TOKEN?.trim()),
  running: false,
  ownerConfigured: Boolean(process.env.TELEGRAM_OWNER_CHAT_ID?.trim()),
  receivedCount: 0,
};

let receiver: TelegramReceiver | undefined;

export function getTelegramReceiverStatus(): ReceiverStatus {
  return { ...status };
}

export function startTelegramReceiver(): void {
  if (receiver) {
    return;
  }

  const token = process.env.TELEGRAM_BOT_TOKEN?.trim();
  if (!token) {
    logger.warn(
      "Telegram file receiver is disabled because TELEGRAM_BOT_TOKEN is not configured",
    );
    return;
  }

  receiver = new TelegramReceiver(token);
  void receiver.start().catch((error: unknown) => {
    status.running = false;
    logger.error({ err: error }, "Telegram file receiver stopped unexpectedly");
  });
}

function parseMaxFileSize(): number {
  const configured = Number(process.env.TELEGRAM_MAX_FILE_SIZE_BYTES);
  if (Number.isFinite(configured) && configured > 0) {
    return Math.floor(configured);
  }
  return DEFAULT_MAX_FILE_SIZE;
}

class TelegramReceiver {
  private readonly token: string;
  private readonly apiBase: string;
  private readonly configuredOwnerChatId?: string;
  private state: ReceiverState = {};
  private nextOffset: number | undefined;
  private stopped = false;

  constructor(token: string) {
    this.token = token;
    this.apiBase = `${TELEGRAM_API_BASE}/bot${token}`;
    this.configuredOwnerChatId = readChatIdFromEnvironment();
  }

  async start(): Promise<void> {
    await mkdir(receiverDirectory, { recursive: true });
    this.state = await readState();

    if (this.configuredOwnerChatId) {
      this.state.ownerChatId = this.configuredOwnerChatId;
      await writeState(this.state);
    }

    const bot = await this.apiCall<{ username?: string; first_name?: string }>(
      "getMe",
    );
    status.ownerConfigured = Boolean(this.state.ownerChatId);
    status.running = true;

    logger.info(
      {
        botUsername: bot.username,
        receiverDirectory,
        maxFileSize,
        ownerConfigured: status.ownerConfigured,
      },
      "Telegram file receiver started",
    );

    process.once("SIGTERM", () => this.stop());
    process.once("SIGINT", () => this.stop());
    await this.poll();
  }

  private stop(): void {
    this.stopped = true;
    status.running = false;
  }

  private async poll(): Promise<void> {
    while (!this.stopped) {
      try {
        const updates = await this.apiCall<TelegramUpdate[]>("getUpdates", {
          offset: this.nextOffset,
          timeout: LONG_POLL_SECONDS,
          allowed_updates: ["message"],
        });

        for (const update of updates) {
          this.nextOffset = update.update_id + 1;
          try {
            await this.handleUpdate(update);
          } catch (error: unknown) {
            logger.error(
              { err: error, updateId: update.update_id },
              "Could not process Telegram update",
            );
          }
        }
      } catch (error: unknown) {
        if (this.stopped) {
          return;
        }

        logger.error({ err: error }, "Telegram polling failed; retrying");
        await delay(5_000);
      }
    }
  }

  private async handleUpdate(update: TelegramUpdate): Promise<void> {
    const message = update.message;
    if (!message || message.chat.type !== "private" || message.from?.is_bot) {
      return;
    }

    const chatId = String(message.chat.id);
    const text = message.text?.trim() ?? "";

    if (!this.state.ownerChatId) {
      if (!/^\/start(?:@\w+)?(?:\s|$)/i.test(text)) {
        await this.sendMessage(
          chatId,
          "Send /start first to securely claim this private file receiver.",
        );
        return;
      }

      this.state.ownerChatId = chatId;
      status.ownerConfigured = true;
      await writeState(this.state);
      await this.sendMessage(
        chatId,
        "File receiver ready. Send files, photos, videos, audio, or voice messages here. Your chat is now the private owner.",
      );
      return;
    }

    if (chatId !== this.state.ownerChatId) {
      await this.sendMessage(chatId, "This file receiver is private.");
      return;
    }

    if (/^\/(?:start|help)(?:@\w+)?(?:\s|$)/i.test(text)) {
      await this.sendMessage(
        chatId,
        "Send any files you want to receive. Documents, photos, videos, audio, voice messages, and animations are saved automatically.",
      );
      return;
    }

    if (/^\/status(?:@\w+)?(?:\s|$)/i.test(text)) {
      await this.sendMessage(
        chatId,
        `Receiver is online. Files saved this session: ${status.receivedCount}.`,
      );
      return;
    }

    const attachment = getAttachment(message);
    if (!attachment) {
      await this.sendMessage(
        chatId,
        "Please send a file, photo, video, audio, voice message, or animation. Use /help for instructions.",
      );
      return;
    }

    if (attachment.fileSize && attachment.fileSize > maxFileSize) {
      await this.sendMessage(
        chatId,
        `That file is too large. The current limit is ${formatBytes(maxFileSize)}.`,
      );
      return;
    }

    await this.receiveAttachment(chatId, message, attachment);
  }

  private async receiveAttachment(
    chatId: string,
    message: TelegramMessage,
    attachment: Attachment,
  ): Promise<void> {
    const file = await this.apiCall<TelegramFile>("getFile", {
      file_id: attachment.fileId,
    });

    if (!file.file_path) {
      throw new Error("Telegram did not return a downloadable file path");
    }

    const response = await fetch(
      `${TELEGRAM_FILE_BASE}/bot${this.token}/${file.file_path}`,
    );
    if (!response.ok) {
      throw new Error(
        `Telegram file download failed with HTTP ${response.status}`,
      );
    }

    const bytes = Buffer.from(await response.arrayBuffer());
    if (bytes.length > maxFileSize) {
      await this.sendMessage(
        chatId,
        `That file is too large. The current limit is ${formatBytes(maxFileSize)}.`,
      );
      return;
    }

    const storedName = makeStoredName(
      attachment.originalName,
      message.message_id,
    );
    await writeFile(path.join(receiverDirectory, storedName), bytes);

    const metadata = {
      receivedAt: new Date().toISOString(),
      messageId: message.message_id,
      chatId,
      kind: attachment.kind,
      originalName: attachment.originalName,
      storedName,
      mimeType: attachment.mimeType ?? null,
      bytes: bytes.length,
      caption: message.caption ?? null,
    };
    await appendFile(metadataPath, `${JSON.stringify(metadata)}\n`, "utf8");

    status.receivedCount += 1;
    await this.sendMessage(
      chatId,
      `Saved ${attachment.originalName} (${formatBytes(bytes.length)}).`,
    );
    logger.info(
      {
        messageId: message.message_id,
        kind: attachment.kind,
        storedName,
        bytes: bytes.length,
      },
      "Telegram file received",
    );
  }

  private async sendMessage(chatId: string, text: string): Promise<void> {
    await this.apiCall("sendMessage", {
      chat_id: chatId,
      text,
    });
  }

  private async apiCall<T>(
    method: string,
    body: Record<string, unknown> = {},
  ): Promise<T> {
    const params = new URLSearchParams();
    for (const [key, value] of Object.entries(body)) {
      if (value !== undefined) {
        params.set(key, typeof value === "string" ? value : JSON.stringify(value));
      }
    }

    const response = await fetch(`${this.apiBase}/${method}`, {
      method: "POST",
      headers: { "content-type": "application/x-www-form-urlencoded" },
      body: params,
    });
    const payload = (await response.json()) as TelegramResponse<T>;

    if (!response.ok || !payload.ok || payload.result === undefined) {
      throw new Error(
        `Telegram ${method} failed: ${payload.description ?? response.statusText}`,
      );
    }
    return payload.result;
  }
}

function readChatIdFromEnvironment(): string | undefined {
  const configured = process.env.TELEGRAM_OWNER_CHAT_ID?.trim();
  if (!configured) {
    return undefined;
  }
  if (!/^-?\d+$/.test(configured)) {
    throw new Error("TELEGRAM_OWNER_CHAT_ID must be a numeric Telegram chat ID");
  }
  return configured;
}

async function readState(): Promise<ReceiverState> {
  try {
    const raw = await readFile(statePath, "utf8");
    const parsed: unknown = JSON.parse(raw);
    if (
      typeof parsed === "object" &&
      parsed !== null &&
      "ownerChatId" in parsed &&
      typeof parsed.ownerChatId === "string"
    ) {
      return { ownerChatId: parsed.ownerChatId };
    }
  } catch (error: unknown) {
    if (isFileNotFound(error)) {
      return {};
    }
    logger.warn({ err: error }, "Could not read Telegram receiver state");
  }
  return {};
}

async function writeState(state: ReceiverState): Promise<void> {
  const tempPath = `${statePath}.tmp`;
  await writeFile(tempPath, `${JSON.stringify(state, null, 2)}\n`, "utf8");
  await rename(tempPath, statePath);
}

function getAttachment(message: TelegramMessage): Attachment | undefined {
  if (message.document) {
    return {
      fileId: message.document.file_id,
      originalName: message.document.file_name ?? `document-${message.message_id}`,
      kind: "document",
      mimeType: message.document.mime_type,
      fileSize: message.document.file_size,
    };
  }

  if (message.photo && message.photo.length > 0) {
    const photo = message.photo[message.photo.length - 1];
    return {
      fileId: photo.file_id,
      originalName: `photo-${message.message_id}.jpg`,
      kind: "photo",
      mimeType: "image/jpeg",
      fileSize: photo.file_size,
    };
  }

  const mediaTypes: Array<
    [keyof Pick<TelegramMessage, "video" | "audio" | "voice" | "animation">, string, string]
  > = [
    ["video", "video", ".mp4"],
    ["audio", "audio", ".mp3"],
    ["voice", "voice", ".ogg"],
    ["animation", "animation", ".mp4"],
  ];

  for (const [field, kind, extension] of mediaTypes) {
    const media = message[field];
    if (media) {
      return {
        fileId: media.file_id,
        originalName: media.file_name ?? `${kind}-${message.message_id}${extension}`,
        kind,
        mimeType: media.mime_type,
        fileSize: media.file_size,
      };
    }
  }

  return undefined;
}

function makeStoredName(originalName: string, messageId: number): string {
  const safeName = path
    .basename(originalName)
    .normalize("NFKC")
    .replace(/[^a-zA-Z0-9._-]/g, "_")
    .replace(/^\.+/, "")
    .slice(0, 180);
  return `${messageId}-${safeName || "received-file"}`;
}

function formatBytes(bytes: number): string {
  if (bytes < 1024) return `${bytes} B`;
  if (bytes < 1024 * 1024) return `${(bytes / 1024).toFixed(1)} KB`;
  return `${(bytes / (1024 * 1024)).toFixed(1)} MB`;
}

function isFileNotFound(error: unknown): boolean {
  return (
    typeof error === "object" &&
    error !== null &&
    "code" in error &&
    error.code === "ENOENT"
  );
}

function delay(milliseconds: number): Promise<void> {
  return new Promise((resolve) => {
    setTimeout(resolve, milliseconds);
  });
}