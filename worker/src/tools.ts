/** Alati radara. Definisani jednom; koriste ih agent 4 (Telegram asistent) i MCP server. */
import type { ToolSpec } from "./anthropic";
import {
  STATUSES,
  type Status,
  TYPES,
  getOpportunity,
  searchOpportunities,
  setReminder,
  trackerAdd,
  trackerList,
} from "./data";
import type { Ctx } from "./env";
import { dispatchRadar } from "./github";
import { getCurrentProfile } from "./profile";
import { proposeAndAsk } from "./proposal";

export interface ToolContext extends Ctx {
  chatId: string;
  /** id-jevi oglasa koje su alati vratili (za linkove iz baze u odgovoru) */
  seen?: Set<number>;
}

export interface Tool {
  spec: ToolSpec;
  run(c: ToolContext, args: Record<string, unknown>): Promise<unknown>;
}

function str(a: Record<string, unknown>, k: string, required = true): string | undefined {
  const v = a[k];
  if (v === undefined || v === null || v === "") {
    if (required) throw new Error(`nedostaje '${k}'`);
    return undefined;
  }
  if (typeof v !== "string") throw new Error(`'${k}' mora biti string`);
  return v;
}

function int(a: Record<string, unknown>, k: string): number {
  const v = a[k];
  const n = typeof v === "string" ? Number(v) : v;
  if (typeof n !== "number" || !Number.isInteger(n) || n <= 0) throw new Error(`'${k}' mora biti ceo broj`);
  return n;
}

const remember = (c: ToolContext, rows: Array<{ id: number }>) => rows.forEach((r) => c.seen?.add(r.id));

export const TOOLS: Tool[] = [
  {
    spec: {
      name: "search_opportunities",
      description:
        "Pretraži oglase u bazi po reči(ma) iz naslova, firme ili opisa. Vraća najviše 10, sa ocenom i razlogom.",
      input_schema: {
        type: "object",
        properties: {
          query: { type: "string", description: "reči za pretragu, npr. 'ML python'" },
          type: { type: "string", enum: [...TYPES] },
          location: { type: "string" },
          deadline_before: { type: "string", description: "YYYY-MM-DD" },
        },
      },
    },
    async run(c, a) {
      const rows = await searchOpportunities(c.sql, {
        query: str(a, "query", false),
        type: str(a, "type", false),
        location: str(a, "location", false),
        deadline_before: str(a, "deadline_before", false),
      });
      remember(c, rows as unknown as Array<{ id: number }>);
      return rows;
    },
  },
  {
    spec: {
      name: "get_opportunity",
      description: "Detalji jednog oglasa: opis, ocena, zašto je ocenjen tako i status prijave.",
      input_schema: { type: "object", properties: { id: { type: "integer" } }, required: ["id"] },
    },
    async run(c, a) {
      const row = await getOpportunity(c.sql, int(a, "id"));
      if (!row) return { error: "oglas ne postoji" };
      remember(c, [row as unknown as { id: number }]);
      const { url: _omit, ...rest } = row as Record<string, unknown>; // link dodaje kod, ne model
      return rest;
    },
  },
  {
    spec: {
      name: "get_profile",
      description: "Trenutna verzija korisnikovog profila (interesovanja, isključenja, lokacije, prag).",
      input_schema: { type: "object", properties: {} },
    },
    async run(c) {
      return (await getCurrentProfile(c.sql)) ?? { error: "profil još nije postavljen" };
    },
  },
  {
    spec: {
      name: "update_profile",
      description:
        "Predloži izmenu profila rečima (npr. 'dodaj embedded, bez frontenda'). Ne menja ništa dok korisnik ne potvrdi dugmetom.",
      input_schema: {
        type: "object",
        properties: { change_text: { type: "string" } },
        required: ["change_text"],
      },
    },
    async run(c, a) {
      const p = await proposeAndAsk(c, c.chatId, str(a, "change_text")!);
      return p
        ? { status: "čeka potvrdu korisnika", pending_id: p.pendingId, izmene: p.changes }
        : { status: "bez promene" };
    },
  },
  {
    spec: {
      name: "tracker_add",
      description: `Upiši ili promeni status prijave za oglas. Status: ${STATUSES.join(", ")}.`,
      input_schema: {
        type: "object",
        properties: { id: { type: "integer" }, status: { type: "string", enum: [...STATUSES] } },
        required: ["id", "status"],
      },
    },
    async run(c, a) {
      return trackerAdd(c.sql, int(a, "id"), str(a, "status")! as Status);
    },
  },
  {
    spec: {
      name: "tracker_list",
      description: "Lista prijava, opciono samo za jedan status.",
      input_schema: { type: "object", properties: { status: { type: "string", enum: [...STATUSES] } } },
    },
    async run(c, a) {
      const rows = await trackerList(c.sql, str(a, "status", false));
      remember(c, rows as unknown as Array<{ id: number }>);
      return rows;
    },
  },
  {
    spec: {
      name: "set_reminder",
      description: "Postavi podsetnik za oglas u zadato vreme (datum YYYY-MM-DD ili ISO).",
      input_schema: {
        type: "object",
        properties: { id: { type: "integer" }, when: { type: "string" }, text: { type: "string" } },
        required: ["id", "when", "text"],
      },
    },
    async run(c, a) {
      return setReminder(c.sql, int(a, "id"), str(a, "when")!, str(a, "text")!);
    },
  },
  {
    spec: {
      name: "run_radar",
      description: "Pokreni rundu radara odmah (digest stiže za ~2 min).",
      input_schema: { type: "object", properties: {} },
    },
    async run(c) {
      return dispatchRadar(c);
    },
  },
];

export const TOOL_MAP = new Map(TOOLS.map((t) => [t.spec.name, t]));
