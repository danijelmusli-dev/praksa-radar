import type { Env } from "./env";
import type { TgUpdate } from "./telegram";

/** Poređenje u konstantnom vremenu (bez ranog izlaza). */
export function safeEqual(a: string, b: string): boolean {
  const enc = new TextEncoder();
  const x = enc.encode(a);
  const y = enc.encode(b);
  let diff = x.length ^ y.length;
  for (let i = 0; i < Math.max(x.length, y.length); i++) diff |= (x[i] ?? 0) ^ (y[i] ?? 0);
  return diff === 0;
}

/** Webhook prihvata samo zahteve sa tajnim `X-Telegram-Bot-Api-Secret-Token` zaglavljem. */
export function hasValidSecret(req: Request, env: Env): boolean {
  const given = req.headers.get("X-Telegram-Bot-Api-Secret-Token") ?? "";
  return env.TELEGRAM_SECRET_TOKEN !== "" && safeEqual(given, env.TELEGRAM_SECRET_TOKEN);
}

/** Samo moj chat_id (privatni chat: chat.id === from.id). */
export function isAllowedChat(update: TgUpdate, env: Env): boolean {
  const allowed = env.ALLOWED_CHAT_ID;
  if (!allowed) return false;
  const chat = update.message?.chat.id ?? update.callback_query?.message?.chat.id;
  const from = update.message?.from?.id ?? update.callback_query?.from.id;
  return chat !== undefined && String(chat) === allowed && String(from ?? chat) === allowed;
}
