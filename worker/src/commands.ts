import { runAssistant } from "./agent";
import {
  STATUSES,
  type Status,
  getKv,
  opportunityExists,
  setKv,
  trackerAdd,
  trackerList,
} from "./data";
import type { Ctx } from "./env";
import { dispatchRadar } from "./github";
import { agentsEnabled, setAgentsEnabled } from "./killswitch";
import { formatProfile, getCurrentProfile, setThreshold, undoProfile } from "./profile";
import { proposeAndAsk } from "./proposal";
import { DRAFT_KINDS, startTask } from "./tasks";
import { sendMessage } from "./telegram";

export const HELP = `\
Praksa Radar. Komande:
/filter — prikaži profil
/filter dodaj: <tekst> | /filter ukloni: <tekst> — izmena rečima (uz potvrdu)
/filter undo — vrati prethodnu verziju
/prag <0-10> — prag relevantnosti
/izvori — status izvora
/sad — pokreni rundu odmah
/analiza <id> — gap analiza oglasa (~1 min)
/nacrt <id> pismo|poruka — nacrt prijave (~1 min; šalješ ti, ne bot)
/projekti sync — osveži moje projekte sa GitHub-a
/prijavio <id> — upiši prijavu
/intervju <id> <YYYY-MM-DD> — intervju (podsetnik dan ranije)
/tracker [status] — lista prijava
/agenti — poslednjih 10 pokretanja agenata
/stop, /pokreni — isključi/uključi agente
Bez "/" pišeš asistentu, npr. "nađi ML prakse sa rokom ove nedelje".`;

const fmtDate = (iso: string | null | undefined) => (iso ? iso.slice(0, 10) : "—");

/** Telegram šalje i `/komanda@ime_bota`; ovo skida sufiks. */
export function parseCommand(text: string): { cmd: string; arg: string } {
  const [head = "", ...rest] = text.trim().split(/\s+/);
  const cmd = head.slice(1).split("@")[0]!.toLowerCase();
  return { cmd, arg: rest.join(" ").trim() };
}

async function filterCommand(ctx: Ctx, chat: string, arg: string): Promise<void> {
  if (!arg) {
    const cur = await getCurrentProfile(ctx.sql);
    return sendMessage(
      ctx,
      chat,
      cur ? formatProfile(cur.profile, cur.id) : "Profil još nije postavljen (python -m radar.cli set-profile).",
    );
  }
  if (arg.toLowerCase() === "undo") {
    const r = await undoProfile(ctx.sql);
    return sendMessage(
      ctx,
      chat,
      r ? `Vraćeno na sadržaj verzije ${r.from} (nova verzija ${r.version}).` : "Nema prethodne verzije.",
    );
  }
  const m = /^(dodaj|ukloni)\s*:\s*(.+)$/is.exec(arg);
  if (!m) return sendMessage(ctx, chat, "Koristi: /filter dodaj: <tekst>, /filter ukloni: <tekst> ili /filter undo");
  const verb = m[1]!.toLowerCase() === "dodaj" ? "Dodaj u profil" : "Ukloni iz profila";
  await proposeAndAsk(ctx, chat, `${verb}: ${m[2]!.trim()}`);
}

async function sourcesCommand(ctx: Ctx, chat: string, arg: string): Promise<void> {
  if (arg.toLowerCase().startsWith("traži") || arg.toLowerCase().startsWith("trazi")) {
    return sendMessage(ctx, chat, "Otkrivanje izvora (agent 2) stiže u Fazi 5.");
  }
  const rows = await ctx.sql`
    select source,
      max(started_at) as last_run,
      max(started_at) filter (where error is null) as last_ok,
      (array_agg(items_found order by started_at desc))[1] as last_items,
      (array_agg(error order by started_at desc))[1] as last_error
    from source_runs group by source order by source`;
  if (!rows.length) return sendMessage(ctx, chat, "Još nijedna runda nije pokrenuta.");
  const lines = rows.map((r) => {
    const ok = r.last_error === null;
    return `${ok ? "✅" : "❌"} ${r.source}: ${r.last_items} stavki, poslednja uspešna ${fmtDate(
      r.last_ok?.toISOString?.() ?? null,
    )}${ok ? "" : ` (greška: ${String(r.last_error).slice(0, 80)})`}`;
  });
  await sendMessage(ctx, chat, lines.join("\n"));
}

async function trackerCommand(ctx: Ctx, chat: string, arg: string): Promise<void> {
  if (arg && !STATUSES.includes(arg as Status)) {
    return sendMessage(ctx, chat, `Status je jedan od: ${STATUSES.join(", ")}`);
  }
  const rows = await trackerList(ctx.sql, arg || undefined);
  if (!rows.length) return sendMessage(ctx, chat, "Tracker je prazan.");
  const by = new Map<string, string[]>();
  for (const r of rows) {
    const extra = [r.deadline && `rok ${fmtDate(r.deadline)}`, r.next_action_at && `termin ${fmtDate(r.next_action_at)}`]
      .filter(Boolean)
      .join(", ");
    (by.get(r.status) ?? by.set(r.status, []).get(r.status)!).push(
      `#${r.id} ${r.title}${r.org ? ` — ${r.org}` : ""}${extra ? ` (${extra})` : ""}`,
    );
  }
  await sendMessage(ctx, chat, [...by].map(([s, l]) => `${s.toUpperCase()}\n${l.join("\n")}`).join("\n\n"));
}

async function agentsCommand(ctx: Ctx, chat: string): Promise<void> {
  const rows = await ctx.sql`
    select id::int as id, agent, started_at, steps, outcome, input_tokens + output_tokens as tokens
    from agent_runs order by id desc limit 10`;
  if (!rows.length) return sendMessage(ctx, chat, "Još nije bilo pokretanja agenata.");
  await sendMessage(
    ctx,
    chat,
    rows
      .map((r) => `#${r.id} ${r.agent} · ${r.steps} koraka · ${r.tokens} tokena · ${r.outcome ?? "u toku"}`)
      .join("\n"),
  );
}

const needId = (arg: string): number | null => {
  const n = Number(arg.split(/\s+/)[0]?.replace(/^#/, ""));
  return Number.isInteger(n) && n > 0 ? n : null;
};

export async function handleCommand(ctx: Ctx, chat: string, text: string): Promise<void> {
  const { cmd, arg } = parseCommand(text);
  switch (cmd) {
    case "start":
    case "help":
      return sendMessage(ctx, chat, HELP);
    case "filter":
      return filterCommand(ctx, chat, arg);
    case "prag": {
      const v = Number(arg.replace(",", "."));
      if (!arg || Number.isNaN(v) || v < 0 || v > 10) return sendMessage(ctx, chat, "Koristi: /prag <0-10>");
      const version = await setThreshold(ctx.sql, v);
      return sendMessage(
        ctx,
        chat,
        version ? `Prag je ${v} (profil verzija ${version}). Važi od sledeće runde.` : "Profil još nije postavljen.",
      );
    }
    case "izvori":
      return sourcesCommand(ctx, chat, arg);
    case "sad": {
      const r = await dispatchRadar(ctx);
      return sendMessage(ctx, chat, r.ok ? r.message : `Nije uspelo: ${r.message}`);
    }
    case "stop":
      await setAgentsEnabled(ctx, false);
      return sendMessage(ctx, chat, "Agenti su isključeni. Pipeline radara radi i dalje. /pokreni ih vraća.");
    case "pokreni":
      await setAgentsEnabled(ctx, true);
      return sendMessage(
        ctx,
        chat,
        (await agentsEnabled(ctx))
          ? "Agenti su uključeni."
          : "Uključeno u bazi, ali je repo varijabla AGENTS_ENABLED=false i dalje gasi agente.",
      );
    case "prijavio": {
      const id = needId(arg);
      if (!id) return sendMessage(ctx, chat, "Koristi: /prijavio <id>");
      if (!(await opportunityExists(ctx.sql, id))) return sendMessage(ctx, chat, `Oglas #${id} ne postoji.`);
      await trackerAdd(ctx.sql, id, "prijavljen");
      return sendMessage(ctx, chat, `Upisano: #${id} prijavljen. Javiću ako za 7 dana ne bude promene.`);
    }
    case "intervju": {
      const [rawId, date] = arg.split(/\s+/);
      const id = needId(rawId ?? "");
      const when = date ? new Date(date) : null;
      if (!id || !when || Number.isNaN(when.getTime())) {
        return sendMessage(ctx, chat, "Koristi: /intervju <id> <YYYY-MM-DD>");
      }
      if (!(await opportunityExists(ctx.sql, id))) return sendMessage(ctx, chat, `Oglas #${id} ne postoji.`);
      await trackerAdd(ctx.sql, id, "intervju", { nextActionAt: when.toISOString() });
      return sendMessage(ctx, chat, `Upisano: #${id} intervju ${fmtDate(when.toISOString())}. Podsetnik stiže dan ranije.`);
    }
    case "tracker":
      return trackerCommand(ctx, chat, arg);
    case "agenti":
      return agentsCommand(ctx, chat);
    case "analiza": {
      const id = needId(arg);
      if (!id) return sendMessage(ctx, chat, "Koristi: /analiza <id>");
      await startTask(ctx, chat, { task: "analyze", id }, "Analiza");
      return;
    }
    case "nacrt": {
      const [rawId, rawKind] = arg.split(/\s+/);
      const id = needId(rawId ?? "");
      const kind = DRAFT_KINDS[(rawKind ?? "").toLowerCase()];
      if (!id || !kind) return sendMessage(ctx, chat, "Koristi: /nacrt <id> pismo|poruka");
      await startTask(ctx, chat, { task: "draft", id, kind }, "Nacrt");
      return;
    }
    case "projekti": {
      if (arg.toLowerCase() !== "sync") return sendMessage(ctx, chat, "Koristi: /projekti sync");
      await startTask(ctx, chat, { task: "projects_sync" }, "Sinhronizacija projekata");
      return;
    }
    case "post":
      return sendMessage(ctx, chat, "/post <repo> stiže u Fazi 5.");
    default:
      return sendMessage(ctx, chat, `Nepoznata komanda.\n\n${HELP}`);
  }
}

/** Slobodan tekst (bez "/") ide asistentu. */
export async function handleFreeText(ctx: Ctx, chat: string, text: string): Promise<void> {
  const result = await runAssistant({ ...ctx, chatId: chat, seen: new Set() }, text);
  await sendMessage(ctx, chat, result.text);
}

export { getKv, setKv };
