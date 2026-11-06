import { getKv, setKv } from "./data";
import type { Ctx } from "./env";
import { githubKillSwitchOn } from "./github";

/** /stop piše agents_enabled=false u kv_state; rezervno repo varijabla AGENTS_ENABLED=false. */
export async function agentsEnabled(ctx: Ctx): Promise<boolean> {
  if ((await getKv(ctx.sql, "agents_enabled")) === false) return false;
  return !(await githubKillSwitchOn(ctx));
}

export const setAgentsEnabled = (ctx: Ctx, on: boolean) => setKv(ctx.sql, "agents_enabled", on);
