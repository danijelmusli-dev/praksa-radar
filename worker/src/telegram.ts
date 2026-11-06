import type { Ctx } from "./env";

export const MAX_LEN = 4096;

export interface InlineButton {
  text: string;
  callback_data: string;
}
export type ReplyMarkup = { inline_keyboard: InlineButton[][] };

export interface TgUpdate {
  update_id: number;
  message?: { chat: { id: number }; from?: { id: number }; text?: string };
  callback_query?: {
    id: string;
    from: { id: number };
    data?: string;
    message?: { chat: { id: number }; message_id: number };
  };
}

const URL_RE = /(?:https?:\/\/|www\.)\S+/gi;

/** Tekst modela ide bez linkova (zaštita od ubrizganog phishing-a). Linkove dodaje samo kod, iz baze. */
export function stripLinks(text: string): string {
  return text.replace(URL_RE, "").replace(/[ \t]+\n/g, "\n").replace(/[ \t]{2,}/g, " ").trim();
}

async function call(ctx: Ctx, method: string, body: Record<string, unknown>): Promise<void> {
  const res = await ctx.fetch(`https://api.telegram.org/bot${ctx.env.TELEGRAM_BOT_TOKEN}/${method}`, {
    method: "POST",
    headers: { "content-type": "application/json" },
    body: JSON.stringify(body),
  });
  if (!res.ok) throw new Error(`Telegram ${method}: ${res.status}`);
}

/** Čist tekst, bez parse_mode. */
export async function sendMessage(
  ctx: Ctx,
  chatId: string | number,
  text: string,
  markup?: ReplyMarkup,
): Promise<void> {
  await call(ctx, "sendMessage", {
    chat_id: chatId,
    text: text.slice(0, MAX_LEN),
    disable_web_page_preview: true,
    ...(markup ? { reply_markup: markup } : {}),
  });
}

export async function answerCallback(ctx: Ctx, id: string, text: string): Promise<void> {
  await call(ctx, "answerCallbackQuery", { callback_query_id: id, text: text.slice(0, 200) });
}

/** Ukloni dugmiće sa poruke (npr. posle potvrde izmene). */
export async function clearButtons(ctx: Ctx, chatId: number, messageId: number): Promise<void> {
  await call(ctx, "editMessageReplyMarkup", {
    chat_id: chatId,
    message_id: messageId,
    reply_markup: { inline_keyboard: [] },
  });
}
