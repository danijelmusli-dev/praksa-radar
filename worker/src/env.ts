import type postgres from "postgres";

export type Sql = postgres.Sql<{}>;

export interface Env {
  HYPERDRIVE: { connectionString: string };
  TELEGRAM_BOT_TOKEN: string;
  TELEGRAM_SECRET_TOKEN: string;
  ALLOWED_CHAT_ID: string;
  ANTHROPIC_API_KEY: string;
  GITHUB_TOKEN?: string;
  GITHUB_REPO?: string;
  MCP_TOKEN?: string;
  RERANK_MODEL?: string;
  AGENT_MODEL?: string;
}

/** Sve što handleri trebaju; fetch i vreme se ubacuju radi testiranja. */
export interface Ctx {
  sql: Sql;
  env: Env;
  fetch: typeof fetch;
  now: () => Date;
}
