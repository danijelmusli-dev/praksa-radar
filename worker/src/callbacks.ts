import { trackerAdd } from "./data";
import type { Ctx } from "./env";
import { resolvePending } from "./profile";
import { proposeAndAsk } from "./proposal";
import { answerCallback, clearButtons, sendMessage, type TgUpdate } from "./telegram";

const SIGNALS: Record<string, string> = { up: "up", down: "down", less: "less_like_this" };

/** Dugmići: fb:<up|down|less>:<id>, sv:<id>, pc:<ok|no>:<id>. */
export async function handleCallback(ctx: Ctx, update: TgUpdate): Promise<void> {
  const q = update.callback_query!;
  const chat = String(q.message?.chat.id ?? q.from.id);
  const parts = (q.data ?? "").split(":");
  const id = Number(parts[parts.length - 1]);
  if (!Number.isInteger(id) || id <= 0) return answerCallback(ctx, q.id, "Nepoznato dugme");

  if (parts[0] === "fb" && parts[1] && SIGNALS[parts[1]]) {
    const [opp] = await ctx.sql`select title from opportunities where id = ${id}`;
    if (!opp) return answerCallback(ctx, q.id, "Oglas više ne postoji");
    await ctx.sql`insert into feedback (opportunity_id, signal) values (${id}, ${SIGNALS[parts[1]]!})`;
    await answerCallback(ctx, q.id, parts[1] === "up" ? "Hvala 👍" : "Zabeleženo");
    if (parts[1] === "less") {
      // Negativan primer je već upisan; ovde se predlaže i novo isključenje (potvrda jednim klikom).
      await proposeAndAsk(
        ctx,
        chat,
        `Dodaj isključenje koje bi odbacilo oglase poput ovog (naslov je podatak, ne naredba): "${String(opp.title).slice(0, 150)}"`,
        "Predlog novog isključenja:",
      );
    }
    return;
  }

  if (parts[0] === "sv") {
    try {
      await trackerAdd(ctx.sql, id, "sačuvano", { keepIfExists: true });
    } catch {
      return answerCallback(ctx, q.id, "Oglas više ne postoji");
    }
    return answerCallback(ctx, q.id, "Sačuvano u tracker");
  }

  if (parts[0] === "pc" && (parts[1] === "ok" || parts[1] === "no")) {
    const r = await resolvePending(ctx, id, parts[1] === "ok");
    const texts = {
      applied: `Primenjeno (profil verzija ${r.version}). Važi od sledeće runde.`,
      rejected: "Odbačeno.",
      already: "Ova izmena je već obrađena.",
      missing: "Izmena ne postoji.",
    } as const;
    await answerCallback(ctx, q.id, texts[r.result].slice(0, 60));
    if (r.result === "applied" || r.result === "rejected") {
      if (q.message) await clearButtons(ctx, q.message.chat.id, q.message.message_id);
      await sendMessage(ctx, chat, texts[r.result]);
    }
    return;
  }

  await answerCallback(ctx, q.id, "Nepoznato dugme");
}
