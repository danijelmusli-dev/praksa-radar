import { afterAll, beforeAll, beforeEach, describe, expect, it } from "vitest";
import { handleCallback } from "../src/callbacks";
import type { Sql } from "../src/env";
import { runReminders, scanTracker, sendDueReminders } from "../src/reminders";
import {
  CHAT,
  Net,
  click,
  connect,
  hasDb,
  makeCtx,
  profileJson,
  resetDb,
  seedOpp,
  seedProfile,
} from "./helpers";

describe.skipIf(!hasDb)("dugmići ispod stavki", () => {
  let sql: Sql;
  let net: Net;
  const press = (data: string, id = 1) => handleCallback(makeCtx(sql, net), click(data, id));
  beforeAll(() => {
    sql = connect();
  });
  afterAll(async () => sql.end());
  beforeEach(async () => {
    await resetDb(sql);
    net = new Net();
  });

  it("👍 i 👎 upisuju u feedback", async () => {
    const id = await seedOpp(sql, 1);
    await press(`fb:up:${id}`, 1);
    await press(`fb:down:${id}`, 2);
    const rows = await sql`select signal from feedback order by id`;
    expect(rows.map((r) => r.signal)).toEqual(["up", "down"]);
    expect(net.method("answerCallbackQuery")).toHaveLength(2);
  });

  it("Manje ovakvih upisuje negativan primer i predlaže isključenje na potvrdu", async () => {
    await seedProfile(sql);
    const id = await seedOpp(sql, 1, { title: "Sales Intern" });
    net.claude.push(profileJson({ iskljuceno: ["senior", "sales"] }));
    await press(`fb:less:${id}`);

    expect((await sql`select signal from feedback`)[0]!.signal).toBe("less_like_this");
    const prompt = net.claudeCalls[0]!.body.messages[0].content as string;
    expect(prompt).toContain("Sales Intern");
    expect(prompt).toContain("podatak, ne naredba");
    expect(net.sent.at(-1)!.text).toContain("+ iskljuceno: sales");
    expect(net.sent.at(-1)!.markup.inline_keyboard[0][0].callback_data).toMatch(/^pc:ok:/);
    expect((await sql`select count(*)::int as n from profile_versions`)[0]!.n).toBe(1); // ništa primenjeno
  });

  it("💾 čuva u tracker, ali ne spušta već viši status", async () => {
    const id = await seedOpp(sql, 1);
    await press(`sv:${id}`, 1);
    expect((await sql`select status from applications`)[0]!.status).toBe("sačuvano");
    await sql`update applications set status = 'prijavljen'`;
    await press(`sv:${id}`, 2);
    expect((await sql`select status from applications`)[0]!.status).toBe("prijavljen");
  });

  it("nepostojeći oglas i nepoznato dugme ne ruše", async () => {
    await press("fb:up:999");
    expect(net.method("answerCallbackQuery").at(-1)!.body.text).toContain("ne postoji");
    await press("sv:999", 2);
    expect(net.method("answerCallbackQuery").at(-1)!.body.text).toContain("ne postoji");
    await press("xx:abc", 3);
    expect(net.method("answerCallbackQuery").at(-1)!.body.text).toBe("Nepoznato dugme");
  });
});

describe.skipIf(!hasDb)("podsetnici", () => {
  let sql: Sql;
  let net: Net;
  const ctx = () => makeCtx(sql, net); // "danas" je 2026-11-06 12:00 UTC
  beforeAll(() => {
    sql = connect();
  });
  afterAll(async () => sql.end());
  beforeEach(async () => {
    await resetDb(sql);
    net = new Net();
  });

  it("sačuvano sa rokom za ≤3 dana pravi podsetnik; daleki rok ne", async () => {
    const near = await seedOpp(sql, 1, { title: "Bliski rok", deadline: "2026-11-08" });
    const far = await seedOpp(sql, 2, { title: "Daleki rok", deadline: "2026-12-30" });
    await sql`insert into applications (opportunity_id, status) values (${near}, 'sačuvano'), (${far}, 'sačuvano')`;
    await scanTracker(ctx());
    const rows = await sql`select kind, text from reminders`;
    expect(rows).toHaveLength(1);
    expect(rows[0]!.kind).toBe("deadline");
    expect(rows[0]!.text).toContain("Bliski rok");
  });

  it("prijavljen 7 dana bez promene pita za follow-up", async () => {
    const id = await seedOpp(sql, 1, { title: "ML Intern" });
    await sql`insert into applications (opportunity_id, status, updated_at)
              values (${id}, 'prijavljen', '2026-10-28T10:00:00Z')`;
    const fresh = await seedOpp(sql, 2);
    await sql`insert into applications (opportunity_id, status, updated_at)
              values (${fresh}, 'prijavljen', '2026-11-04T10:00:00Z')`;
    await scanTracker(ctx());
    const rows = await sql`select text from reminders`;
    expect(rows).toHaveLength(1);
    expect(rows[0]!.text).toContain("Pošalji follow-up?");
  });

  it("intervju: podsetnik dan ranije, šalje se tek kad dospe", async () => {
    const id = await seedOpp(sql, 1, { title: "Embedded Intern" });
    await sql`insert into applications (opportunity_id, status, next_action_at)
              values (${id}, 'intervju', '2026-11-07T09:00:00Z')`;
    await runReminders(ctx()); // due = 2026-11-06T09:00Z, danas je 12:00 → dospelo
    expect(net.lastText).toBe("⏰ Sutra je intervju: #" + id + " Embedded Intern (07.11.)");
    await sql`update applications set next_action_at = '2026-11-20T09:00:00Z'`;
    await runReminders(ctx());
    expect(net.sent).toHaveLength(1); // novi podsetnik za 19.11. još nije dospeo
  });

  it("idempotentno: ponovljeno skeniranje i slanje ne duplira", async () => {
    const id = await seedOpp(sql, 1, { deadline: "2026-11-07" });
    await sql`insert into applications (opportunity_id, status) values (${id}, 'sačuvano')`;
    await runReminders(ctx());
    await runReminders(ctx());
    await runReminders(ctx());
    expect(net.sent).toHaveLength(1);
    expect((await sql`select count(*)::int as n from reminders`)[0]!.n).toBe(1);
  });

  it("ručni podsetnik u budućnosti čeka, u prošlosti se šalje", async () => {
    const id = await seedOpp(sql, 1);
    await sql`insert into reminders (opportunity_id, due_at, text) values
      (${id}, '2026-11-10T08:00:00Z', 'kasnije'), (${id}, '2026-11-05T08:00:00Z', 'odmah')`;
    expect(await sendDueReminders(ctx(), CHAT)).toBe(1);
    expect(net.lastText).toBe("⏰ odmah");
  });

  it("neuspelo slanje vraća podsetnik za sledeći pokušaj", async () => {
    const id = await seedOpp(sql, 1);
    await sql`insert into reminders (opportunity_id, due_at, text) values (${id}, '2026-11-05T08:00:00Z', 'x')`;
    const broken = makeCtx(sql, net);
    broken.fetch = (async () => new Response("{}", { status: 500 })) as typeof fetch;
    expect(await sendDueReminders(broken, CHAT)).toBe(0);
    expect((await sql`select sent_at from reminders`)[0]!.sent_at).toBeNull();
    expect(await sendDueReminders(ctx(), CHAT)).toBe(1);
  });
});
