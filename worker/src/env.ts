import type postgres from "postgres";

export type Sql = postgres.Sql<{}>;

export interface Env {
  HYPERDRIVE: { connectionString: string };
  TELEGRAM_BOT_TOKEN: string;
  TELEGRAM_SECRET_TOKEN: string;
  ALLOWED_CHAT_ID: string;
  GEMINI_API_KEY?: string; // besplatan nivo (aistudio.google.com)
  ANTHROPIC_API_KEY?: string;
  LLM_PROVIDER?: string; // "gemini" | "anthropic"; prazno = gemini ako ima ključ
  GITHUB_TOKEN?: string;
  GITHUB_REPO?: string;
  MCP_TOKEN?: string;
  RERANK_MODEL?: string;
  AGENT_MODEL?: string;
  WATCHDOG?: string; // "on" kad radi cron: javlja ako nema uspešne runde
  STALE_HOURS?: string;
}

/** Sve što handleri trebaju; fetch i vreme se ubacuju radi testiranja. */
export interface Ctx {
  sql: Sql;
  env: Env;
  fetch: typeof fetch;
  now: () => Date;
}
