import { afterAll, beforeAll, beforeEach, describe, expect, it } from "vitest";
import { handleCallback } from "../src/callbacks";
import { handleCommand } from "../src/commands";
import type { Sql } from "../src/env";
import { checkStale, runReminders } from "../src/reminders";
import { CHAT, Net, click, connect, hasDb, makeCtx, resetDb } from "./helpers";

describe.skipIf(!hasDb)("Faza 5: agenti, predlozi izvora, watchdog", () => {
  let sql: Sql;
  let net: Net;
  const dispatches = () => net.calls.filter((c) => c.url.endsWith("/dispatches")).map((c) => c.body);
  const cmd = (text: string) => handleCommand(makeCtx(sql, net), CHAT, text);
  const press = (data: string, id = 1) => handleCallback(makeCtx(sql, net), click(data, id));
  const proposal = async (status = "pending") => {
    const [row] = await sql`insert into source_proposals (name, url, payload, status)
      values ('novi-sajt', 'https://novi.rs/prakse', '{}'::jsonb, ${status}) returning id::int as id`;
    return row!.id as number;
  };
  beforeAll(() => {
    sql = connect();
  });
  afterAll(async () => sql.end());
  beforeEach(async () => {
    await resetDb(sql);
    net = new Net();
  });

  it("/izvori traži pokreće agenta 2 (event agent), osim kad su agenti isključeni", async () => {
    await cmd("/izvori traži");
    expect(dispatches()).toEqual([{ event_type: "agent", client_payload: { task: "discover" } }]);
    await sql`insert into kv_state (key, value) values ('agents_enabled', 'false'::jsonb)`;
    await cmd("/izvori traži");
    expect(net.lastText).toContain("isključeni");
    expect(dispatches()).toHaveLength(1);
  });

  it("/post <repo> šalje zadatak, a loša imena odbija", async () => {
    await cmd("/post praksa-radar");
    expect(dispatches()).toEqual([
      { event_type: "task", client_payload: { task: "post", repo: "praksa-radar" } },
    ]);
    for (const bad of ["", "..", "a/b", "ime sa razmakom".replace(" ", "/"), "x".repeat(101)]) {
      await cmd(`/post ${bad}`);
      expect(net.lastText).toContain("Koristi: /post");
    }
    expect(dispatches()).toHaveLength(1);
  });

  it("✅ Dodaj potvrđuje predlog i pokreće PR; drugi klik ništa ne radi", async () => {
    const id = await proposal();
    await press(`sa:${id}`, 1);
    expect(dispatches()).toEqual([
      { event_type: "agent", client_payload: { task: "add_source", id } },
    ]);
    expect((await sql`select status from source_proposals`)[0]!.status).toBe("accepted");
    expect(net.method("editMessageReplyMarkup")).toHaveLength(1);

    await press(`sa:${id}`, 2);
    expect(net.method("answerCallbackQuery").at(-1)!.body.text).toContain("već obrađen");
    expect(dispatches()).toHaveLength(1);
  });

  it("❌ Odbaci zatvara predlog bez PR-a; nepostojeći predlog ne ruši", async () => {
    const id = await proposal();
    await press(`sr:${id}`);
    expect((await sql`select status from source_proposals`)[0]!.status).toBe("rejected");
    expect(dispatches()).toEqual([]);
    await press("sa:999", 2);
    expect(net.method("answerCallbackQuery").at(-1)!.body.text).toContain("ne postoji");
  });

  it("odbijen predlog se ne može naknadno potvrditi (status nije pending)", async () => {
    const id = await proposal("rejected");
    await press(`sa:${id}`);
    expect(dispatches()).toEqual([]);
  });

  describe("watchdog (cron)", () => {
    const setLast = (iso: string | null) =>
      iso
        ? sql`insert into kv_state (key, value) values ('last_successful_run', ${sql.json(iso as never)})`
        : Promise.resolve();
    const ctx = (env: Record<string, string>) => makeCtx(sql, net, env); // "sada" = 2026-11-06T12:00Z

    it("isključen po defaultu (ručne runde ne alarmiraju)", async () => {
      await setLast("2026-11-01T10:00:00Z");
      expect(await checkStale(ctx({}))).toBe(false);
      expect(net.sent).toHaveLength(0);
    });

    it("javlja kad nema runde duže od 26 h, jednom dnevno", async () => {
      await setLast("2026-11-05T09:00:00Z"); // 27 h
      expect(await checkStale(ctx({ WATCHDOG: "on" }))).toBe(true);
      expect(net.lastText).toContain("Nema uspešne runde već 27 h");
      expect(await checkStale(ctx({ WATCHDOG: "on" }))).toBe(false); // isti dan: bez duplog alarma
      expect(net.sent).toHaveLength(1);
    });

    it("ćuti kad je runda sveža ili nikad nije radila", async () => {
      await setLast("2026-11-06T06:00:00Z"); // 6 h
      expect(await checkStale(ctx({ WATCHDOG: "on" }))).toBe(false);
      await resetDb(sql);
      expect(await checkStale(ctx({ WATCHDOG: "on" }))).toBe(false);
      expect(net.sent).toHaveLength(0);
    });

    it("poštuje STALE_HOURS i radi u cron putanji (runReminders)", async () => {
      await setLast("2026-11-06T06:00:00Z"); // 6 h
      await runReminders(ctx({ WATCHDOG: "on", STALE_HOURS: "4" }));
      expect(net.lastText).toContain("Nema uspešne runde već 6 h");
    });
  });
});
