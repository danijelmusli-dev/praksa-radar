import postgres from "postgres";
import { hasValidSecret, isAllowedChat } from "./auth";
import { handleCallback } from "./callbacks";
import { handleCommand, handleFreeText } from "./commands";
import { getKv, setKv } from "./data";
import type { Ctx, Env } from "./env";
import { handleMcp } from "./mcp";
import { runReminders } from "./reminders";
import { sendMessage, type TgUpdate } from "./telegram";

/** Obrada jednog update-a. Vrati false ako je ignorisan (tuđ chat ili već viđen). */
export async function handleUpdate(ctx: Ctx, update: TgUpdate): Promise<boolean> {
  if (!isAllowedChat(update, ctx.env)) return false; // tuđe poruke se tiho ignorišu

  const last = Number(((await getKv(ctx.sql, "tg_offset")) as number | null) ?? 0);
  if (update.update_id <= last) return false; // Telegram ponavlja isporuku; obrađujemo jednom
  await setKv(ctx.sql, "tg_offset", update.update_id);

  const chat = ctx.env.ALLOWED_CHAT_ID;
  try {
    if (update.callback_query) {
      await handleCallback(ctx, update);
    } else if (update.message?.text) {
      const text = update.message.text.trim();
      await (text.startsWith("/") ? handleCommand(ctx, chat, text) : handleFreeText(ctx, chat, text));
    }
  } catch (e) {
    console.error("greška u obradi update-a", e);
    await sendMessage(ctx, chat, "Došlo je do greške pri obradi. Pokušaj ponovo.").catch(() => {});
  }
  return true;
}

function makeCtx(env: Env): { ctx: Ctx; close: () => Promise<void> } {
  const sql = postgres(env.HYPERDRIVE.connectionString, { max: 5, fetch_types: false, prepare: false } /* Hyperdrive/pooler: bez prepared statements */);
  return {
    ctx: { sql, env, fetch: (i, o) => fetch(i, o), now: () => new Date() },
    close: () => sql.end({ timeout: 2 }),
  };
}

export default {
  async fetch(req: Request, env: Env, exec: ExecutionContext): Promise<Response> {
    const url = new URL(req.url);

    if (url.pathname === "/health") return new Response("ok");

    if (url.pathname === "/telegram" && req.method === "POST") {
      if (!hasValidSecret(req, env)) return new Response("Forbidden", { status: 403 });
      const update = (await req.json()) as TgUpdate;
      const { ctx, close } = makeCtx(env);
      // Odmah 200, obrada ide u pozadini (asistent zna da traje).
      exec.waitUntil(handleUpdate(ctx, update).finally(close));
      return new Response("ok");
    }

    if (url.pathname === "/mcp") {
      const { ctx, close } = makeCtx(env);
      try {
        return await handleMcp(ctx, req);
      } finally {
        exec.waitUntil(close());
      }
    }

    return new Response("Not found", { status: 404 });
  },

  async scheduled(_event: ScheduledController, env: Env, exec: ExecutionContext): Promise<void> {
    const { ctx, close } = makeCtx(env);
    exec.waitUntil(runReminders(ctx).finally(close));
  },
};
