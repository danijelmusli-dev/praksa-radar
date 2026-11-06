import { afterAll, beforeAll, beforeEach, describe, expect, it } from "vitest";
import { ANOMALY_STEPS, MAX_STEPS, TOKEN_BUDGET, runAssistant } from "../src/agent";
import type { Sql } from "../src/env";
import { handleMcp } from "../src/mcp";
import { TOOLS } from "../src/tools";
import {
  CHAT,
  Net,
  connect,
  hasDb,
  makeCtx,
  resetDb,
  say,
  seedOpp,
  seedProfile,
  useTool,
} from "./helpers";

describe.skipIf(!hasDb)("agent 4 (Telegram asistent)", () => {
  let sql: Sql;
  let net: Net;
  const ask = (text: string, opts = {}) =>
    runAssistant({ ...makeCtx(sql, net), chatId: CHAT, seen: new Set() }, text, opts);
  beforeAll(() => {
    sql = connect();
  });
  afterAll(async () => sql.end());
  beforeEach(async () => {
    await resetDb(sql);
    net = new Net();
  });

  it("pozove alat, odgovori, linkove dodaje samo iz baze, a model-tekst bez linkova", async () => {
    const id = await seedOpp(sql, 1, { title: "ML Intern", text: "python ML praksa" });
    net.claude.push(
      useTool("t1", "search_opportunities", { query: "ML" }),
      say(`Našao sam #${id} ML Intern. Pogledaj https://evil.example/phish i www.zlo.com`),
    );
    const r = await ask("nađi ML prakse");
    expect(r.outcome).toBe("ok");
    expect(r.text).not.toContain("evil.example");
    expect(r.text).not.toContain("zlo.com");
    expect(r.text).toContain(`#${id} ML Intern`);
    expect(r.text).toContain(`#${id} https://x.rs/1`); // jedini link: iz kolone url

    // rezultat alata ide modelu kao nepouzdan podatak, bez url kolone
    const toolResult = JSON.stringify(net.claudeCalls[1]!.body.messages.at(-1));
    expect(toolResult).toContain("nepouzdano");
    expect(toolResult).not.toContain("https://x.rs/1");

    const [run] = await sql`select agent, steps, outcome, input_tokens, trace from agent_runs`;
    expect(run).toMatchObject({ agent: "telegram-asistent", steps: 2, outcome: "ok", input_tokens: 200 });
    expect(run!.trace[0]).toMatchObject({ tool: "search_opportunities" });
  });

  it("link se ne dodaje za id koji alat nije vratio (model ne može da ga izmisli)", async () => {
    await seedOpp(sql, 1);
    net.claude.push(say("Evo #1, to je to."));
    const r = await ask("šta ima?");
    expect(r.text).toBe("Evo #1, to je to.");
  });

  it("greška alata je opservacija, ne ruši proces", async () => {
    net.claude.push(useTool("t1", "get_opportunity", { id: "nije-broj" }), say("Ne mogu da nađem."));
    const r = await ask("detalji");
    expect(r.outcome).toBe("ok");
    const result = JSON.stringify(net.claudeCalls[1]!.body.messages.at(-1));
    expect(result).toContain("mora biti ceo broj");
    expect(result).toContain('"is_error":true');
  });

  it("alat van liste se odbija i prijavljuje kao anomalija", async () => {
    net.claude.push(useTool("t1", "run_radar"), say("U redu."));
    const r = await ask("pokreni", { allowed: new Set(["get_profile"]) });
    expect(r.outcome).toBe("ok");
    expect(net.calls.some((c) => c.url.endsWith("/dispatches"))).toBe(false);
    expect(JSON.stringify(net.claudeCalls[1]!.body.messages.at(-1))).toContain("nije dozvoljen");
    expect(net.sent.some((m) => m.text.includes("poziv odbijenog alata"))).toBe(true);
  });

  it("isti alat sa istim argumentima 3 puta prekida petlju", async () => {
    for (let i = 0; i < 5; i++) net.claude.push(useTool(`t${i}`, "get_profile"));
    const r = await ask("vrti se");
    expect(r.outcome).toBe("petlja");
    expect(r.text).toContain("vrteo u krug");
    expect(net.claudeCalls).toHaveLength(3);
    expect(net.sent.some((m) => m.text.includes("prekid: petlja"))).toBe(true);
  });

  it("limit od 15 koraka", async () => {
    for (let i = 0; i < 20; i++) net.claude.push(useTool(`t${i}`, "get_opportunity", { id: i + 1 }));
    const r = await ask("beskonačno");
    expect(r.outcome).toBe("limit koraka");
    expect(r.steps).toBe(MAX_STEPS);
    expect(ANOMALY_STEPS).toBeLessThan(MAX_STEPS);
    expect(net.sent.some((m) => m.text.includes("više od 10 koraka"))).toBe(true);
  });

  it("budžet tokena prekida rad i javlja na Telegram", async () => {
    const big = (id: string) => () => ({
      content: [{ type: "tool_use", id, name: "get_profile", input: { n: id } }],
      stop_reason: "tool_use",
      usage: { input_tokens: TOKEN_BUDGET, output_tokens: 1 },
    });
    net.claude.push(big("a"), big("b"));
    const r = await ask("skupo");
    expect(r.outcome).toBe("budžet");
    expect(net.claudeCalls).toHaveLength(1);
    expect(net.sent.some((m) => m.text.includes("prekid: budžet"))).toBe(true);
  });

  it("kill switch se proverava pre svakog koraka, ne samo na početku", async () => {
    net.claude.push(useTool("t1", "get_profile"), say("ne bi smelo da stigne ovde"));
    const orig = net.fetch;
    let first = true;
    net.fetch = async (i, o) => {
      const res = await orig(i, o);
      if (String(i).includes("anthropic") && first) {
        first = false; // /stop stigne dok se prvi korak izvršava
        await sql`insert into kv_state (key, value) values ('agents_enabled', 'false'::jsonb)`;
      }
      return res;
    };
    const r = await ask("profil?");
    expect(r.outcome).toBe("ugašen");
    expect(net.claudeCalls).toHaveLength(1);
  });

  it("ugašen agent ne zove model uopšte (kv i GitHub varijabla)", async () => {
    await sql`insert into kv_state (key, value) values ('agents_enabled', 'false'::jsonb)`;
    expect((await ask("hej")).outcome).toBe("ugašen");
    await sql`delete from kv_state`;
    net.killVar = "false";
    expect((await ask("hej")).outcome).toBe("ugašen");
    expect(net.claudeCalls).toHaveLength(0);
    net.killVar = "true";
    net.claude.push(say("živ sam"));
    expect((await ask("hej")).outcome).toBe("ok");
  });

  it("Anthropic greška daje pristojnu poruku", async () => {
    net.claude.push(() => new Response("{}", { status: 529 }));
    const r = await ask("hej");
    expect(r.outcome).toBe("greška");
    expect(r.text).toContain("nije dostupan");
    expect((await sql`select outcome from agent_runs`)[0]!.outcome).toBe("greška");
  });

  it("update_profile samo predlaže (pending_changes), profil ostaje isti", async () => {
    const v = await seedProfile(sql);
    net.claude.push(
      useTool("t1", "update_profile", { change_text: "dodaj embedded" }),
      () => ({
        content: [{ type: "text", text: JSON.stringify({ interesovanja: ["python", "ML", "embedded"], iskljuceno: ["senior"], lokacije: ["Beograd", "remote"], tipovi: ["praksa", "radionica"], nivo: "student", jezici: ["srpski"], prag_relevantnosti: 7 }) }],
        stop_reason: "end_turn",
        usage: { input_tokens: 5, output_tokens: 5 },
      }),
      say("Poslao sam predlog, potvrdi dugmetom."),
    );
    // redosled poziva: agent(update_profile) → profil LLM → agent(final)
    const r = await ask("dodaj embedded u filter");
    expect(r.outcome).toBe("ok");
    expect((await sql`select status from pending_changes`)[0]!.status).toBe("pending");
    expect((await sql`select max(id)::int as v from profile_versions`)[0]!.v).toBe(v);
    expect(net.sent.some((m) => m.markup?.inline_keyboard?.[0]?.[0]?.callback_data?.startsWith("pc:ok:"))).toBe(true);
  });

  it("alati rade preko uloga: reader ne može da piše, tracker ne može da briše", async () => {
    const { withRole } = await import("../src/data");
    await expect(
      withRole(sql, "agent_reader", (tx) => tx`insert into feedback (opportunity_id, signal) values (1, 'up')`),
    ).rejects.toThrow(/permission denied/);
    await seedOpp(sql, 1);
    await expect(withRole(sql, "agent_tracker", (tx) => tx`delete from applications`)).rejects.toThrow(
      /permission denied/,
    );
  });
});

describe.skipIf(!hasDb)("MCP server", () => {
  let sql: Sql;
  let net: Net;
  const j = async (r: Response) => (await r.json()) as any;
  const rpc = (body: unknown, token: string | null = "mcp-token", method = "POST") =>
    handleMcp(
      makeCtx(sql, net),
      new Request("https://w/mcp", {
        method,
        headers: { "content-type": "application/json", ...(token ? { authorization: `Bearer ${token}` } : {}) },
        body: method === "POST" ? JSON.stringify(body) : undefined,
      }),
    );
  beforeAll(() => {
    sql = connect();
  });
  afterAll(async () => sql.end());
  beforeEach(async () => {
    await resetDb(sql);
    net = new Net();
  });

  it("odbija bez ili sa pogrešnim tokenom; isključen bez MCP_TOKEN", async () => {
    expect((await rpc({}, null)).status).toBe(401);
    expect((await rpc({}, "pogresan")).status).toBe(401);
    const off = await handleMcp(makeCtx(sql, net, { MCP_TOKEN: undefined }), new Request("https://w/mcp", { method: "POST" }));
    expect(off.status).toBe(404);
    expect((await rpc({}, "mcp-token", "GET")).status).toBe(405);
  });

  it("initialize, ping, tools/list sa 8 alata", async () => {
    const init = await j(await rpc({ jsonrpc: "2.0", id: 1, method: "initialize", params: {} }));
    expect(init.result.serverInfo.name).toBe("praksa-radar");
    expect(init.result.capabilities.tools).toBeDefined();
    expect((await rpc({ jsonrpc: "2.0", method: "notifications/initialized" })).status).toBe(202);
    expect((await j(await rpc({ jsonrpc: "2.0", id: 2, method: "ping" }))).result).toEqual({});

    const list = await j(await rpc({ jsonrpc: "2.0", id: 3, method: "tools/list" }));
    expect(list.result.tools.map((t: { name: string }) => t.name).sort()).toEqual(
      [
        "get_opportunity", "get_profile", "run_radar", "search_opportunities",
        "set_reminder", "tracker_add", "tracker_list", "update_profile",
      ],
    );
    expect(list.result.tools.every((t: { inputSchema: unknown }) => t.inputSchema)).toBe(true);
    expect(TOOLS).toHaveLength(8);
  });

  it("tools/call: pretraga, tracker, greška alata kao isError, nepoznat alat", async () => {
    const id = await seedOpp(sql, 1, { title: "ML Intern" });
    const call = async (name: string, args: unknown) =>
      (await j(await rpc({ jsonrpc: "2.0", id: 9, method: "tools/call", params: { name, arguments: args } })));

    const found = await call("search_opportunities", { query: "ML" });
    expect(JSON.parse(found.result.content[0].text)[0].title).toBe("ML Intern");

    await call("tracker_add", { id, status: "prijavljen" });
    const listed = JSON.parse((await call("tracker_list", {})).result.content[0].text);
    expect(listed[0]).toMatchObject({ id, status: "prijavljen" });

    const bad = await call("tracker_add", { id, status: "nepoznat" });
    expect(bad.result.isError).toBe(true);
    expect((await call("nema_alata", {})).error.code).toBe(-32602);
    expect((await j(await rpc({ jsonrpc: "2.0", id: 1, method: "foo" }))).error.code).toBe(-32601);
  });

  it("set_reminder validira datum", async () => {
    const id = await seedOpp(sql, 1);
    const call = async (args: unknown) =>
      (await j(await rpc({ jsonrpc: "2.0", id: 1, method: "tools/call", params: { name: "set_reminder", arguments: args } })));
    expect((await call({ id, when: "sutra", text: "x" })).result.isError).toBe(true);
    expect((await call({ id, when: "2026-11-20", text: "pozovi" })).result.isError).toBeUndefined();
    expect((await sql`select text from reminders`)[0]!.text).toBe("pozovi");
  });
});
