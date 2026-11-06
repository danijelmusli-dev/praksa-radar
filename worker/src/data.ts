/** Upiti nad bazom. Agent i MCP rade kroz uloge sa najmanjim privilegijama (set local role). */
import type { Sql } from "./env";

export const TYPES = ["praksa", "projekat", "radionica", "takmičenje", "posao", "nebitno"] as const;
export const STATUSES = ["sačuvano", "prijavljen", "intervju", "ponuda", "odbijen", "odustao"] as const;
export type Status = (typeof STATUSES)[number];

type Role = "agent_reader" | "agent_tracker";

export async function withRole<T>(sql: Sql, role: Role, fn: (tx: Sql) => Promise<T>): Promise<T> {
  if (role !== "agent_reader" && role !== "agent_tracker") throw new Error("nepoznata uloga");
  return (await sql.begin(async (tx) => {
    await tx.unsafe(`set local role ${role}`);
    return fn(tx as unknown as Sql);
  })) as T;
}

export interface SearchArgs {
  query?: string;
  type?: string;
  location?: string;
  deadline_before?: string;
}

const like = (s: string) => `%${s.replace(/[\\%_]/g, (m) => `\\${m}`)}%`;

export async function searchOpportunities(sql: Sql, a: SearchArgs) {
  if (a.type && !(TYPES as readonly string[]).includes(a.type)) throw new Error(`nepoznat tip: ${a.type}`);
  if (a.deadline_before && !/^\d{4}-\d{2}-\d{2}$/.test(a.deadline_before)) {
    throw new Error("deadline_before mora biti YYYY-MM-DD");
  }
  const words = (a.query ?? "").split(/\s+/).filter(Boolean).slice(0, 6);
  return withRole(sql, "agent_reader", async (tx) => {
    const hay = tx`(o.title || ' ' || coalesce(o.org, '') || ' ' || coalesce(o.raw_text, ''))`;
    const parts = words.map((w) => tx`(case when ${hay} ilike ${like(w)} then 1 else 0 end)`);
    const score = parts.length ? parts.reduce((x, y) => tx`${x} + ${y}`) : tx`0`;
    return tx`
      select o.id::int as id, o.title, o.org, o.location, o.type, o.deadline::text as deadline,
             s.relevance, left(s.reason, 200) as reason
      from opportunities o left join scores s on s.opportunity_id = o.id
      where ${words.length ? tx`${score} > 0` : tx`true`}
        ${a.type ? tx`and o.type = ${a.type}` : tx``}
        ${a.location ? tx`and o.location ilike ${like(a.location)}` : tx``}
        ${a.deadline_before ? tx`and o.deadline <= ${a.deadline_before}::date` : tx``}
        and (o.deadline is null or o.deadline >= current_date)
      order by ${score} desc, s.relevance desc nulls last, o.first_seen desc
      limit 10`;
  });
}

export async function getOpportunity(sql: Sql, id: number) {
  return withRole(sql, "agent_reader", async (tx) => {
    const [row] = await tx`
      select o.id::int as id, o.title, o.org, o.location, o.type, o.deadline::text as deadline,
             o.url, left(o.raw_text, 1500) as text, s.relevance, s.reason, s.passed, a.status
      from opportunities o
      left join scores s on s.opportunity_id = o.id
      left join applications a on a.opportunity_id = o.id
      where o.id = ${id}`;
    return row ?? null;
  });
}

export async function opportunityExists(sql: Sql, id: number): Promise<boolean> {
  const [row] = await sql`select 1 as x from opportunities where id = ${id}`;
  return Boolean(row);
}

export async function trackerAdd(
  sql: Sql,
  id: number,
  status: Status,
  opts: { nextActionAt?: string; keepIfExists?: boolean } = {},
) {
  if (!STATUSES.includes(status)) throw new Error(`nepoznat status: ${status}`);
  if (!(await opportunityExists(sql, id))) throw new Error(`oglas #${id} ne postoji`);
  return withRole(sql, "agent_tracker", async (tx) => {
    const applied = status === "prijavljen" ? tx`now()` : tx`null`;
    const next = opts.nextActionAt ?? null;
    if (opts.keepIfExists) {
      await tx`insert into applications (opportunity_id, status) values (${id}, ${status})
               on conflict (opportunity_id) do nothing`;
    } else {
      await tx`insert into applications (opportunity_id, status, applied_at, next_action_at)
               values (${id}, ${status}, ${applied}, ${next}::timestamptz)
               on conflict (opportunity_id) do update set status = excluded.status,
                 applied_at = coalesce(applications.applied_at, excluded.applied_at),
                 next_action_at = coalesce(excluded.next_action_at, applications.next_action_at),
                 updated_at = now()`;
    }
    const [row] = await tx`select status from applications where opportunity_id = ${id}`;
    return { id, status: row!.status as string };
  });
}

export async function trackerList(sql: Sql, status?: string) {
  if (status && !STATUSES.includes(status as Status)) throw new Error(`nepoznat status: ${status}`);
  return withRole(sql, "agent_reader", (tx) => tx`
    select a.opportunity_id::int as id, o.title, o.org, a.status, o.deadline::text as deadline,
           a.next_action_at::text as next_action_at, a.updated_at::text as updated_at
    from applications a join opportunities o on o.id = a.opportunity_id
    ${status ? tx`where a.status = ${status}` : tx``}
    order by a.updated_at desc limit 50`);
}

export async function setReminder(sql: Sql, id: number, when: string, text: string) {
  const at = new Date(when);
  if (Number.isNaN(at.getTime())) throw new Error("'when' mora biti datum (YYYY-MM-DD ili ISO)");
  if (!text.trim()) throw new Error("tekst podsetnika je prazan");
  if (!(await opportunityExists(sql, id))) throw new Error(`oglas #${id} ne postoji`);
  return withRole(sql, "agent_tracker", async (tx) => {
    const [row] = await tx`
      insert into reminders (opportunity_id, kind, due_at, text)
      values (${id}, 'manual', ${at.toISOString()}::timestamptz, ${text.slice(0, 300)})
      on conflict (opportunity_id, kind, due_at) do update set text = excluded.text
      returning id::int as id, due_at::text as due_at`;
    return row!;
  });
}

export async function getKv(sql: Sql, key: string): Promise<unknown> {
  const [row] = await sql`select value from kv_state where key = ${key}`;
  return row?.value ?? null;
}

export async function setKv(sql: Sql, key: string, value: unknown): Promise<void> {
  await sql`insert into kv_state (key, value) values (${key}, ${sql.json(value as never)})
            on conflict (key) do update set value = excluded.value`;
}
