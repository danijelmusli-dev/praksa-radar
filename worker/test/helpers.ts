import { readdirSync, readFileSync } from "node:fs";
import { join } from "node:path";
import postgres from "postgres";
import type { Ctx, Env, Sql } from "../src/env";
import type { Profile } from "../src/profile";

export const DB_URL = process.env.TEST_DATABASE_URL;
export const hasDb = Boolean(DB_URL);
export const CHAT = "42";

const MIGRATIONS = join(__dirname, "..", "..", "db", "migrations");

export function connect(): Sql {
  return postgres(DB_URL!, { max: 5, onnotice: () => {}, fetch_types: false, prepare: false });
}

export async function resetDb(sql: Sql): Promise<void> {
  await sql.unsafe("drop schema public cascade; create schema public;");
  for (const f of readdirSync(MIGRATIONS).filter((f) => f.endsWith(".sql")).sort()) {
    await sql.unsafe(readFileSync(join(MIGRATIONS, f), "utf-8"));
  }
  // isto što i `python -m radar.db migrate`: RLS + politike za agent uloge
  await sql.unsafe(readFileSync(join(MIGRATIONS, "..", "lockdown.sql"), "utf-8"));
}

export async function freshDb(): Promise<Sql> {
  const sql = connect();
  await resetDb(sql);
  return sql;
}

export interface Call {
  url: string;
  body: any;
}

type Queued = (body: any) => unknown;

/** Lažni Telegram + Anthropic + GitHub. Anthropic odgovori se redom vade iz reda. */
export class Net {
  calls: Call[] = [];
  claude: Queued[] = [];
  gemini: Queued[] = [];
  dispatchStatus = 204;
  killVar: string | null = null;

  fetch = async (input: RequestInfo | URL, init?: RequestInit): Promise<Response> => {
    const url = String(input);
    const body = init?.body ? JSON.parse(String(init.body)) : undefined;
    this.calls.push({ url, body });
    const json = (o: unknown, status = 200) => new Response(JSON.stringify(o), { status });
    if (url.startsWith("https://api.telegram.org/")) return json({ ok: true });
    if (url === "https://api.anthropic.com/v1/messages") {
      const next = this.claude.shift();
      if (!next) throw new Error("nema više spremnih Anthropic odgovora");
      const out = next(body);
      return out instanceof Response ? out : json(out);
    }
    if (url.startsWith("https://generativelanguage.googleapis.com/")) {
      const next = this.gemini.shift();
      if (!next) throw new Error("nema više spremnih Gemini odgovora");
      const out = next(body);
      return out instanceof Response ? out : json(out);
    }
    if (url.endsWith("/dispatches")) return new Response(null, { status: this.dispatchStatus });
    if (url.endsWith("/actions/variables/AGENTS_ENABLED")) {
      return this.killVar === null ? json({}, 404) : json({ value: this.killVar });
    }
    throw new Error(`neočekivan zahtev: ${url}`);
  };

  /** Poruke poslate korisniku. */
  get sent(): Array<{ text: string; markup?: any }> {
    return this.calls
      .filter((c) => c.url.endsWith("/sendMessage"))
      .map((c) => ({ text: c.body.text as string, markup: c.body.reply_markup }));
  }
  get lastText(): string {
    return this.sent.at(-1)?.text ?? "";
  }
  method(name: string) {
    return this.calls.filter((c) => c.url.endsWith(`/${name}`));
  }
  get claudeCalls() {
    return this.calls.filter((c) => c.url.includes("anthropic"));
  }
  get geminiCalls() {
    return this.calls.filter((c) => c.url.includes("generativelanguage"));
  }
}

export const say = (text: string) => () => ({
  content: [{ type: "text", text }],
  stop_reason: "end_turn",
  usage: { input_tokens: 100, output_tokens: 50 },
});

export const useTool = (id: string, name: string, input: Record<string, unknown> = {}) => () => ({
  content: [{ type: "tool_use", id, name, input }],
  stop_reason: "tool_use",
  usage: { input_tokens: 100, output_tokens: 50 },
});

export function makeCtx(sql: Sql, net: Net, env: Partial<Env> = {}): Ctx {
  return {
    sql,
    fetch: net.fetch as typeof fetch,
    now: () => new Date("2026-11-06T12:00:00Z"),
    env: {
      HYPERDRIVE: { connectionString: DB_URL ?? "" },
      TELEGRAM_BOT_TOKEN: "tok",
      TELEGRAM_SECRET_TOKEN: "sekret",
      ALLOWED_CHAT_ID: CHAT,
      ANTHROPIC_API_KEY: "key",
      GITHUB_TOKEN: "gh",
      GITHUB_REPO: "me/praksa-radar",
      MCP_TOKEN: "mcp-token",
      ...env,
    },
  };
}

export const PROFILE: Profile = {
  interesovanja: ["python", "ML"],
  iskljuceno: ["senior"],
  lokacije: ["Beograd", "remote"],
  tipovi: ["praksa", "radionica"],
  nivo: "student",
  jezici: ["srpski"],
  prag_relevantnosti: 7,
};

export async function seedProfile(sql: Sql, profile: Profile = PROFILE, withEmbedding = false) {
  const [row] = withEmbedding
    ? await sql`insert into profile_versions (raw_text, structured, embedding)
        values ('seed', ${sql.json(profile as never)}, ${"[" + Array(1024).fill(0.01).join(",") + "]"}::vector)
        returning id::int as id`
    : await sql`insert into profile_versions (raw_text, structured) values ('seed', ${sql.json(profile as never)})
        returning id::int as id`;
  return row!.id as number;
}

export async function seedOpp(
  sql: Sql,
  n: number,
  o: { title?: string; deadline?: string | null; type?: string; text?: string; org?: string } = {},
): Promise<number> {
  const [row] = await sql`
    insert into opportunities (url, source, title, org, location, type, deadline, raw_text)
    values (${`https://x.rs/${n}`}, 't', ${o.title ?? `Oglas ${n}`}, ${o.org ?? "Firma"}, 'Beograd',
            ${o.type ?? "praksa"}, ${o.deadline ?? null}, ${o.text ?? "python ML"})
    returning id::int as id`;
  return row!.id as number;
}

export const update = (text: string, id = 1) => ({
  update_id: id,
  message: { chat: { id: Number(CHAT) }, from: { id: Number(CHAT) }, text },
});

export const click = (data: string, id = 1) => ({
  update_id: id,
  callback_query: {
    id: `cb${id}`,
    from: { id: Number(CHAT) },
    data,
    message: { chat: { id: Number(CHAT) }, message_id: 7 },
  },
});

export const profileJson = (p: Partial<Profile>): Queued => () => ({
  content: [{ type: "text", text: JSON.stringify({ ...PROFILE, ...p }) }],
  stop_reason: "end_turn",
  usage: { input_tokens: 10, output_tokens: 10 },
});
