import { afterAll, beforeAll, beforeEach, describe, expect, it } from "vitest";
import { handleCallback } from "../src/callbacks";
import { handleCommand } from "../src/commands";
import type { Sql } from "../src/env";
import { CHAT, Net, click, connect, hasDb, makeCtx, resetDb, seedOpp } from "./helpers";

describe.skipIf(!hasDb)("teški zadaci (Faza 4): komande i dugmići → repository_dispatch", () => {
  let sql: Sql;
  let net: Net;
  const dispatches = () => net.calls.filter((c) => c.url.endsWith("/dispatches")).map((c) => c.body);
  const press = (data: string, id = 1) => handleCallback(makeCtx(sql, net), click(data, id));
  const cmd = (text: string) => handleCommand(makeCtx(sql, net), CHAT, text);
  beforeAll(() => {
    sql = connect();
  });
  afterAll(async () => sql.end());
  beforeEach(async () => {
    await resetDb(sql);
    net = new Net();
  });

  it("/nacrt <id> pismo|poruka i /analiza šalju payload u Actions", async () => {
    const id = await seedOpp(sql, 1);
    await cmd(`/nacrt ${id} pismo`);
    await cmd(`/nacrt ${id} poruka`);
    await cmd(`/analiza ${id}`);
    expect(dispatches()).toEqual([
      { event_type: "task", client_payload: { task: "draft", id, kind: "letter" } },
      { event_type: "task", client_payload: { task: "draft", id, kind: "message" } },
      { event_type: "task", client_payload: { task: "analyze", id } },
    ]);
    expect(net.sent[0]!.text).toContain("rezultat stiže za ~1 min");
  });

  it("/projekti sync", async () => {
    await cmd("/projekti sync");
    expect(dispatches()).toEqual([{ event_type: "task", client_payload: { task: "projects_sync" } }]);
    await cmd("/projekti nešto");
    expect(net.lastText).toBe("Koristi: /projekti sync");
    expect(dispatches()).toHaveLength(1);
  });

  it("neispravne komande i nepostojeći oglas ne pokreću ništa", async () => {
    await cmd("/nacrt 1");
    expect(net.lastText).toContain("Koristi: /nacrt");
    await cmd("/nacrt abc pismo");
    await cmd("/nacrt 1 roman");
    await cmd("/analiza");
    await cmd("/analiza 999");
    expect(net.lastText).toContain("ne postoji");
    expect(dispatches()).toEqual([]);
  });

  it("kad su agenti isključeni (kv ili GitHub varijabla), zadatak se ne šalje", async () => {
    const id = await seedOpp(sql, 1);
    await sql`insert into kv_state (key, value) values ('agents_enabled', 'false'::jsonb)`;
    await cmd(`/analiza ${id}`);
    expect(net.lastText).toContain("isključeni");
    await sql`delete from kv_state`;
    net.killVar = "false";
    await cmd(`/nacrt ${id} pismo`);
    expect(net.lastText).toContain("isključeni");
    expect(dispatches()).toEqual([]);
  });

  it("GitHub odbija: korisnik dobija poruku", async () => {
    const id = await seedOpp(sql, 1);
    net.dispatchStatus = 422;
    await cmd(`/analiza ${id}`);
    expect(net.lastText).toContain("Nije uspelo");
  });

  it("dugme Analiza pokreće analizu", async () => {
    const id = await seedOpp(sql, 1);
    await press(`an:${id}`);
    expect(dispatches()).toEqual([{ event_type: "task", client_payload: { task: "analyze", id } }]);
    expect(net.method("answerCallbackQuery")[0]!.body.text).toContain("analizu");
  });

  it("dugme Nacrt pita pismo ili poruka, pa pokreće izabrano", async () => {
    const id = await seedOpp(sql, 1);
    await press(`nc:${id}`, 1);
    const row = net.sent.at(-1)!.markup.inline_keyboard[0];
    expect(row.map((b: { callback_data: string }) => b.callback_data)).toEqual([
      `dr:l:${id}`,
      `dr:m:${id}`,
    ]);
    expect(dispatches()).toEqual([]); // još ništa dok ne izabereš
    await press(`dr:m:${id}`, 2);
    expect(dispatches()).toEqual([
      { event_type: "task", client_payload: { task: "draft", id, kind: "message" } },
    ]);
    expect(net.method("editMessageReplyMarkup")).toHaveLength(1);
  });

  it("dugme za nepostojeći oglas ne pokreće zadatak", async () => {
    await press("an:999");
    expect(net.lastText).toContain("ne postoji");
    expect(dispatches()).toEqual([]);
  });
});
