import { opportunityExists } from "./data";
import type { Ctx } from "./env";
import { type TaskPayload, dispatchTask } from "./github";
import { agentsEnabled } from "./killswitch";
import { sendMessage } from "./telegram";

export const DRAFT_KINDS: Record<string, "letter" | "message"> = {
  pismo: "letter",
  letter: "letter",
  poruka: "message",
  message: "message",
};

/** Zajednička putanja za komande i dugmiće: provere, dispatch u Actions, odgovor korisniku. */
export async function startTask(
  ctx: Ctx,
  chat: string,
  payload: TaskPayload,
  label: string,
): Promise<boolean> {
  if (!(await agentsEnabled(ctx))) {
    await sendMessage(ctx, chat, "Agenti su isključeni. /pokreni ih uključuje.");
    return false;
  }
  if ("id" in payload && !(await opportunityExists(ctx.sql, payload.id))) {
    await sendMessage(ctx, chat, `Oglas #${payload.id} ne postoji.`);
    return false;
  }
  const r = await dispatchTask(ctx, payload);
  await sendMessage(
    ctx,
    chat,
    r.ok ? `${label}: pokrenuto, rezultat stiže za ~1 min.` : `Nije uspelo: ${r.message}`,
  );
  return r.ok;
}
