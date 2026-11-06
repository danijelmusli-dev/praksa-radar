import { afterAll, beforeAll, beforeEach, describe, expect, it } from "vitest";
import { handleCallback } from "../src/callbacks";
import { handleCommand, parseCommand } from "../src/commands";
import { getCurrentProfile } from "../src/profile";
import {
  CHAT,
  Net,
  PROFILE,
  click,
  connect,
  hasDb,
  makeCtx,
  profileJson,
  resetDb,
  seedOpp,
  seedProfile,
} from "./helpers";
import type { Sql } from "../src/env";

describe("parseCommand", () => {
  it("skida @bot sufiks i razdvaja argument", () => {
    expect(parseCommand("/Filter@praksa_bot dodaj: embedded")).toEqual({ cmd: "filter", arg: "dodaj: embedded" });
    expect(parseCommand("/stop")).toEqual({ cmd: "stop", arg: "" });
  });
});

describe.skipIf(!hasDb)("komande", () => {
  let sql: Sql;
  let net: Net;
  const run = (text: string, env = {}) => handleCommand(makeCtx(sql, net, env), CHAT, text);
  beforeAll(() => {
    sql = connect();
  });
  afterAll(async () => sql.end());
  beforeEach(async () => {
    await resetDb(sql);
    net = new Net();
  });

  it("/filter prikazuje profil, a bez profila objašnjava kako se postavlja", async () => {
    await run("/filter");
    expect(net.lastText).toContain("još nije postavljen");
    await seedProfile(sql);
    await run("/filter");
    expect(net.lastText).toContain("Interesovanja: python, ML");
    expect(net.lastText).toContain("Prag: 7");
  });

  it("/filter dodaj: predlaže izmenu, ništa ne primenjuje dok ne klikneš", async () => {
    const v = await seedProfile(sql);
    net.claude.push(profileJson({ interesovanja: [...PROFILE.interesovanja, "embedded"] }));
    await run("/filter dodaj: embedded");

    const { text, markup } = net.sent.at(-1)!;
    expect(text).toContain("+ interesovanja: embedded");
    const [ok, no] = markup.inline_keyboard[0];
    expect(ok.callback_data).toMatch(/^pc:ok:\d+$/);
    expect(no.callback_data).toMatch(/^pc:no:\d+$/);
    expect((await getCurrentProfile(sql))!.id).toBe(v); // još nema nove verzije
    expect(net.claudeCalls[0]!.body.messages[0].content).toContain("Dodaj u profil: embedded");

    // potvrda
    await handleCallback(makeCtx(sql, net), click(ok.callback_data));
    const cur = (await getCurrentProfile(sql))!;
    expect(cur.id).toBeGreaterThan(v);
    expect(cur.profile.interesovanja).toContain("embedded");
    const [row] = await sql`select embedding is null as emb from profile_versions where id = ${cur.id}`;
    expect(row!.emb).toBe(true); // embedding dopunjuje Python pipeline
    expect(net.method("editMessageReplyMarkup")).toHaveLength(1);

    // drugi klik na isto dugme ne primenjuje ponovo
    await handleCallback(makeCtx(sql, net), click(ok.callback_data, 2));
    expect((await getCurrentProfile(sql))!.id).toBe(cur.id);
    expect(net.method("answerCallbackQuery").at(-1)!.body.text).toContain("već obrađena");
  });

  it("odbacivanje ne menja profil", async () => {
    const v = await seedProfile(sql);
    net.claude.push(profileJson({ iskljuceno: ["senior", "sales"] }));
    await run("/filter dodaj: bez salesa");
    const no = net.sent.at(-1)!.markup.inline_keyboard[0][1].callback_data;
    await handleCallback(makeCtx(sql, net), click(no));
    expect((await getCurrentProfile(sql))!.id).toBe(v);
    expect(net.lastText).toBe("Odbačeno.");
  });

  it("izmena koja ništa ne menja ne pravi predlog", async () => {
    await seedProfile(sql);
    net.claude.push(profileJson({}));
    await run("/filter dodaj: python");
    expect(net.lastText).toContain("nema promene");
    expect((await sql`select 1 from pending_changes`).length).toBe(0);
  });

  it("neispravan izlaz modela se odbacuje, ne popravlja", async () => {
    await seedProfile(sql);
    net.claude.push(() => ({
      content: [{ type: "text", text: '{"interesovanja": "nije lista"}' }],
      stop_reason: "end_turn",
      usage: { input_tokens: 1, output_tokens: 1 },
    }));
    await expect(run("/filter dodaj: x")).rejects.toThrow("lista stringova");
    expect((await sql`select 1 from pending_changes`).length).toBe(0);
  });

  it("/filter bez dodaj/ukloni daje uputstvo", async () => {
    await run("/filter nešto");
    expect(net.lastText).toContain("/filter dodaj:");
  });

  it("/filter undo pravi novu verziju kao kopiju prethodne", async () => {
    await seedProfile(sql);
    const second = await seedProfile(sql, { ...PROFILE, iskljuceno: ["sales"] }, true);
    await run("/filter undo");
    const cur = (await getCurrentProfile(sql))!;
    expect(cur.id).toBeGreaterThan(second);
    expect(cur.profile.iskljuceno).toEqual(["senior"]);
    expect(net.lastText).toContain("Vraćeno");
  });

  it("/filter undo bez istorije", async () => {
    await seedProfile(sql);
    await run("/filter undo");
    expect(net.lastText).toBe("Nema prethodne verzije.");
  });

  it("/prag menja samo prag i čuva embedding", async () => {
    const id = await seedProfile(sql, PROFILE, true);
    await run("/prag 6");
    const cur = (await getCurrentProfile(sql))!;
    expect(cur.profile.prag_relevantnosti).toBe(6);
    expect(cur.profile.interesovanja).toEqual(PROFILE.interesovanja);
    const [row] = await sql`select embedding is not null as ok from profile_versions where id = ${cur.id}`;
    expect(row!.ok).toBe(true);
    expect(cur.id).toBeGreaterThan(id);
    await run("/prag 11");
    expect(net.lastText).toBe("Koristi: /prag <0-10>");
  });

  it("/sad šalje repository_dispatch", async () => {
    await run("/sad");
    const d = net.calls.find((c) => c.url.endsWith("/repos/me/praksa-radar/dispatches"))!;
    expect(d.body).toEqual({ event_type: "run_radar" });
    expect(net.lastText).toContain("Runda pokrenuta");
    net.dispatchStatus = 401;
    await run("/sad");
    expect(net.lastText).toContain("Nije uspelo");
  });

  it("/stop i /pokreni menjaju kill switch u bazi", async () => {
    await run("/stop");
    expect((await sql`select value from kv_state where key = 'agents_enabled'`)[0]!.value).toBe(false);
    await run("/pokreni");
    expect((await sql`select value from kv_state where key = 'agents_enabled'`)[0]!.value).toBe(true);
    expect(net.lastText).toBe("Agenti su uključeni.");
  });

  it("/prijavio, /intervju i /tracker", async () => {
    const id = await seedOpp(sql, 1, { title: "ML Intern", deadline: "2026-11-20" });
    await run(`/prijavio ${id}`);
    expect(net.lastText).toContain("prijavljen");
    await run("/prijavio 999");
    expect(net.lastText).toContain("ne postoji");

    const id2 = await seedOpp(sql, 2, { title: "Embedded Intern" });
    await run(`/intervju ${id2} 2026-11-25`);
    await run("/tracker");
    expect(net.lastText).toContain("PRIJAVLJEN\n#" + id + " ML Intern");
    expect(net.lastText).toContain("INTERVJU\n#" + id2 + " Embedded Intern");
    expect(net.lastText).toContain("termin 2026-11-25");

    await run("/tracker intervju");
    expect(net.lastText).not.toContain("PRIJAVLJEN");
    await run("/tracker nepoznat");
    expect(net.lastText).toContain("Status je jedan od");
    await run("/intervju 1 nije-datum");
    expect(net.lastText).toContain("Koristi: /intervju");
  });

  it("/izvori prikazuje poslednje stanje svakog izvora", async () => {
    await run("/izvori");
    expect(net.lastText).toContain("Još nijedna runda");
    await sql`insert into source_runs (source, items_found, error) values ('helloworld', 30, null)`;
    await sql`insert into source_runs (source, items_found, error) values ('gmail', 0, 'GmailAuthError: istekao')`;
    await run("/izvori");
    expect(net.lastText).toContain("✅ helloworld: 30 stavki");
    expect(net.lastText).toContain("❌ gmail: 0 stavki");
    expect(net.lastText).toContain("GmailAuthError");
  });

  it("/agenti, nepoznata komanda, nacrt placeholder", async () => {
    await run("/agenti");
    expect(net.lastText).toContain("Još nije bilo");
    await run("/nesto");
    expect(net.lastText).toContain("Nepoznata komanda");
    await run("/post nešto");
    expect(net.lastText).toContain("Fazi 5");
  });
});
