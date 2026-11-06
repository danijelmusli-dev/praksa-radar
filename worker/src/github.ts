import type { Ctx } from "./env";

function headers(ctx: Ctx): Record<string, string> {
  return {
    authorization: `Bearer ${ctx.env.GITHUB_TOKEN}`,
    accept: "application/vnd.github+json",
    "user-agent": "praksa-radar-worker",
    "x-github-api-version": "2022-11-28",
  };
}

function configured(ctx: Ctx): boolean {
  return Boolean(ctx.env.GITHUB_TOKEN && ctx.env.GITHUB_REPO);
}

/** Pokreće rundu radara (workflow `radar` sluša repository_dispatch run_radar). */
export async function dispatchRadar(ctx: Ctx): Promise<{ ok: boolean; message: string }> {
  if (!configured(ctx)) return { ok: false, message: "GITHUB_TOKEN ili GITHUB_REPO nije podešen" };
  const res = await ctx.fetch(`https://api.github.com/repos/${ctx.env.GITHUB_REPO}/dispatches`, {
    method: "POST",
    headers: { ...headers(ctx), "content-type": "application/json" },
    body: JSON.stringify({ event_type: "run_radar" }),
  });
  return res.status === 204
    ? { ok: true, message: "Runda pokrenuta, digest stiže za ~2 min." }
    : { ok: false, message: `GitHub je odbio zahtev (${res.status})` };
}

export type TaskPayload =
  | { task: "projects_sync" }
  | { task: "analyze"; id: number }
  | { task: "draft"; id: number; kind: "letter" | "message" };

/** Teški zadaci (analiza, nacrt, sync projekata) idu u Actions: workflow `tasks`, event "task". */
export async function dispatchTask(
  ctx: Ctx,
  payload: TaskPayload,
): Promise<{ ok: boolean; message: string }> {
  if (!configured(ctx)) return { ok: false, message: "GITHUB_TOKEN ili GITHUB_REPO nije podešen" };
  const res = await ctx.fetch(`https://api.github.com/repos/${ctx.env.GITHUB_REPO}/dispatches`, {
    method: "POST",
    headers: { ...headers(ctx), "content-type": "application/json" },
    body: JSON.stringify({ event_type: "task", client_payload: payload }),
  });
  return res.status === 204
    ? { ok: true, message: "pokrenuto" }
    : { ok: false, message: `GitHub je odbio zahtev (${res.status})` };
}

/** Rezervni kill switch: repo varijabla AGENTS_ENABLED=false ugasi agente. Greška API-ja ne gasi. */
export async function githubKillSwitchOn(ctx: Ctx): Promise<boolean> {
  if (!configured(ctx)) return false;
  try {
    const res = await ctx.fetch(
      `https://api.github.com/repos/${ctx.env.GITHUB_REPO}/actions/variables/AGENTS_ENABLED`,
      { headers: headers(ctx) },
    );
    if (!res.ok) return false;
    const body = (await res.json()) as { value?: string };
    return (body.value ?? "").trim().toLowerCase() === "false";
  } catch {
    return false;
  }
}
