/** Tracker podsetnici: pravila iz spec-a + ručni (set_reminder). Poziva se iz cron-a Worker-a. */
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
  await scanTracker(ctx);
  return sendDueReminders(ctx, ctx.env.ALLOWED_CHAT_ID);
}
