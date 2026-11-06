/** Tracker podsetnici: pravila iz spec-a + ručni (set_reminder). Poziva se iz cron-a Worker-a. */
import { getKv, setKv } from "./data";
import type { Ctx } from "./env";
import { sendMessage } from "./telegram";

/** Pravila prave redove u `reminders` (idempotentno: unique po opportunity_id, kind, due_at). */
export async function scanTracker(ctx: Ctx): Promise<void> {
  // Sačuvano, a rok za 3 dana ili manje.
  await ctx.sql`
    insert into reminders (opportunity_id, kind, due_at, text)
    select a.opportunity_id, 'deadline', (o.deadline - 3)::timestamptz,
           'Rok ističe ' || to_char(o.deadline, 'DD.MM.') || ': #' || o.id || ' ' || o.title
    from applications a join opportunities o on o.id = a.opportunity_id
    where a.status = 'sačuvano' and o.deadline is not null
      and o.deadline between ${ctx.now().toISOString().slice(0, 10)}::date
                         and ${ctx.now().toISOString().slice(0, 10)}::date + 3
    on conflict do nothing`;
  // Prijavljen, a 7 dana bez promene.
  await ctx.sql`
    insert into reminders (opportunity_id, kind, due_at, text)
    select a.opportunity_id, 'followup', date_trunc('day', a.updated_at) + interval '7 days',
           '7 dana od prijave bez promene: #' || o.id || ' ' || o.title || '. Pošalji follow-up?'
    from applications a join opportunities o on o.id = a.opportunity_id
    where a.status = 'prijavljen' and a.updated_at <= ${ctx.now().toISOString()}::timestamptz - interval '7 days'
    on conflict do nothing`;
  // Intervju: podsetnik dan ranije.
  await ctx.sql`
    insert into reminders (opportunity_id, kind, due_at, text)
    select a.opportunity_id, 'interview', a.next_action_at - interval '1 day',
           'Sutra je intervju: #' || o.id || ' ' || o.title || ' (' || to_char(a.next_action_at, 'DD.MM.') || ')'
    from applications a join opportunities o on o.id = a.opportunity_id
    where a.status = 'intervju' and a.next_action_at is not null
    on conflict do nothing`;
}

/** Pošalji dospele podsetnike. Prvo se "zauzmu" (sent_at), pa šalju; pad slanja ih vraća. */
export async function sendDueReminders(ctx: Ctx, chatId: string): Promise<number> {
  const claimed = await ctx.sql`
    update reminders set sent_at = now()
    where id in (select id from reminders where sent_at is null and due_at <= ${ctx.now().toISOString()}::timestamptz
                 order by due_at limit 20 for update skip locked)
    returning id::int as id, text`;
  let sent = 0;
  for (const r of claimed) {
    try {
      await sendMessage(ctx, chatId, `⏰ ${r.text}`);
      sent++;
    } catch {
      await ctx.sql`update reminders set sent_at = null where id = ${r.id as number}`;
    }
  }
  return sent;
}

export async function runReminders(ctx: Ctx): Promise<number> {
  await checkStale(ctx).catch((e) => console.error("watchdog", e));
  await scanTracker(ctx);
  return sendDueReminders(ctx, ctx.env.ALLOWED_CHAT_ID);
}

/**
 * Watchdog: ako nema uspešne runde duže od STALE_HOURS, javi (jednom dnevno). Unutrašnji nadzor
 * ne može da javi da je i sam stao, pa Worker (nezavisan od Actions) gleda `last_successful_run`.
 * Uključuje se sa WATCHDOG=on tek kad radi cron (inače bi ručne runde stalno alarmirale).
 */
export async function checkStale(ctx: Ctx): Promise<boolean> {
  if ((ctx.env.WATCHDOG ?? "off").toLowerCase() !== "on") return false;
  const last = (await getKv(ctx.sql, "last_successful_run")) as string | null;
  if (!last) return false;
  const limit = Number(ctx.env.STALE_HOURS ?? 26);
  const hours = (ctx.now().getTime() - Date.parse(last)) / 3_600_000;
  if (!(hours >= limit)) return false;
  const today = ctx.now().toISOString().slice(0, 10);
  if ((await getKv(ctx.sql, "alert:stale")) === today) return false;
  await setKv(ctx.sql, "alert:stale", today);
  await sendMessage(
    ctx,
    ctx.env.ALLOWED_CHAT_ID,
    `⚠ Nema uspešne runde već ${Math.floor(hours)} h (poslednja: ${last.slice(0, 16).replace("T", " ")}). ` +
      "Proveri GitHub Actions (cron se gasi posle 60 dana neaktivnosti, nalog može biti blokiran).",
  );
  return true;
}
