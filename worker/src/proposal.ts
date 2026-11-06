import type { Ctx } from "./env";
import { type Proposal, proposeProfileChange } from "./profile";
import { sendMessage } from "./telegram";

export const confirmButtons = (pendingId: number) => ({
  inline_keyboard: [
    [
      { text: "✅ Potvrdi", callback_data: `pc:ok:${pendingId}` },
      { text: "❌ Odbaci", callback_data: `pc:no:${pendingId}` },
    ],
  ],
});

/** Predlog izmene profila → poruka sa dugmićima. Vraća null ako instrukcija ništa ne menja. */
export async function proposeAndAsk(
  ctx: Ctx,
  chatId: string | number,
  instruction: string,
  intro = "Predlog izmene profila:",
): Promise<Proposal | null> {
  const proposal = await proposeProfileChange(ctx, instruction);
  if (!proposal) {
    await sendMessage(ctx, chatId, "Profil je već takav, nema promene.");
    return null;
  }
  await sendMessage(
    ctx,
    chatId,
    `${intro}\n${proposal.changes.join("\n")}\n\nVaži od sledeće runde tek kad potvrdiš.`,
    confirmButtons(proposal.pendingId),
  );
  return proposal;
}
